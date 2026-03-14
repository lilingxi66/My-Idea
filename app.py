import datetime
import functools
import json
import locale
import os
import os.path as osp
import re
import shutil
import webbrowser

from qtpy import QtCore
from qtpy.QtCore import Qt
from qtpy import QtGui
from qtpy import QtWidgets

from labelme import __appname__
from labelme import PY2
from labelme import QT5

from . import utils
from labelme.config import get_config
from labelme.label_file import LabelFile
from labelme.label_file import LabelFileError
from labelme.logger import logger
from labelme.shape import DEFAULT_FILL_COLOR
from labelme.shape import DEFAULT_LINE_COLOR
from labelme.shape import Shape
from labelme.widgets import Canvas
from labelme.widgets import ColorDialog
from labelme.widgets import EscapableQListWidget
from labelme.widgets import LabelDialog
from labelme.widgets import LabelQListWidget
from labelme.widgets import ToolBar
from labelme.widgets import ZoomWidget


# FIXME
# - [medium] Set max zoom value to something big enough for FitWidth/Window

# TODO(unknown):
# - [high] Add polygon movement with arrow keys
# - [high] Deselect shape when clicking and already selected(?)
# - [low,maybe] Open images with drag & drop.
# - [low,maybe] Preview images on file dialogs.
# - Zoom is too "steppy".


class LabelMappingDialog(QtWidgets.QDialog):

    def __init__(self, preview_callback, parent=None):
        super(LabelMappingDialog, self).__init__(parent)
        self._preview_callback = preview_callback
        self._preview_rows = []
        self._mapping = {}
        self.setWindowTitle('Batch Label Mapping')
        self.resize(720, 520)

        layout = QtWidgets.QVBoxLayout(self)

        help_label = QtWidgets.QLabel(
            "One mapping per line. Supported formats: old=new, old,new, old<TAB>new"
        )
        layout.addWidget(help_label)

        self.editor = QtWidgets.QPlainTextEdit()
        self.editor.setPlaceholderText(
            "Example:\n绿油=green_oil\n裂缝=crack\n污渍=stain"
        )
        layout.addWidget(self.editor)

        button_row = QtWidgets.QHBoxLayout()
        self.previewButton = QtWidgets.QPushButton('Preview')
        self.previewButton.clicked.connect(self.preview)
        button_row.addWidget(self.previewButton)
        button_row.addStretch()
        layout.addLayout(button_row)

        self.summaryLabel = QtWidgets.QLabel('No preview yet.')
        layout.addWidget(self.summaryLabel)

        self.previewTable = QtWidgets.QTableWidget(0, 4)
        self.previewTable.setHorizontalHeaderLabels(
            ['Old Label', 'New Label', 'Polygons', 'Files']
        )
        self.previewTable.horizontalHeader().setStretchLastSection(True)
        self.previewTable.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.Stretch
        )
        self.previewTable.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        self.previewTable.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows
        )
        layout.addWidget(self.previewTable)

        self.buttonBox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)
        layout.addWidget(self.buttonBox)

    def setText(self, text):
        self.editor.setPlainText(text)

    def parseMappings(self):
        mapping = {}
        for line in self.editor.toPlainText().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                old, new = line.split('=', 1)
            elif '\t' in line:
                old, new = line.split('\t', 1)
            elif ',' in line:
                old, new = line.split(',', 1)
            else:
                raise ValueError(
                    "Invalid mapping line '{}'. Use old=new.".format(line)
                )
            old = old.strip()
            new = new.strip()
            if not old or not new:
                raise ValueError(
                    "Invalid mapping line '{}'. Old and new labels are required."
                    .format(line)
                )
            mapping[old] = new
        if not mapping:
            raise ValueError('Please provide at least one label mapping.')
        return mapping

    def preview(self):
        mapping = self.parseMappings()
        rows, summary = self._preview_callback(mapping)
        self._mapping = mapping
        self._preview_rows = rows
        self.summaryLabel.setText(summary)
        self.previewTable.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row['old_label'],
                row['new_label'],
                str(row['polygons']),
                str(row['files']),
            ]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                self.previewTable.setItem(row_index, col, item)
        return rows

    def accept(self):
        try:
            self.preview()
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                'Batch Label Mapping',
                str(e),
            )
            return
        super(LabelMappingDialog, self).accept()

    def getMapping(self):
        return self._mapping

    def getPreviewRows(self):
        return self._preview_rows


class MainWindow(QtWidgets.QMainWindow):

    FIT_WINDOW, FIT_WIDTH, MANUAL_ZOOM = 0, 1, 2

    def __init__(
        self,
        config=None,
        filename=None,
        output=None,
        output_file=None,
        output_dir=None,
    ):
        if output is not None:
            logger.warning(
                'argument output is deprecated, use output_file instead'
            )
            if output_file is None:
                output_file = output

        # see labelme/config/default_config.yaml for valid configuration
        if config is None:
            config = get_config()
        self._config = config

        super(MainWindow, self).__init__()
        self.setWindowTitle(__appname__)

        # Whether we need to save or not.
        self.dirty = False

        self._noSelectionSlot = False

        # Main widgets and related state.
        self.labelDialog = LabelDialog(
            parent=self,
            labels=self._config['labels'],
            sort_labels=self._config['sort_labels'],
            show_text_field=self._config['show_label_text_field'],
            completion=self._config['label_completion'],
            fit_to_content=self._config['fit_to_content'],
            flags=self._config['label_flags']
        )

        self.labelList = LabelQListWidget()
        self.lastOpenDir = None

        self.flag_dock = self.flag_widget = None
        self.flag_dock = QtWidgets.QDockWidget('Flags', self)
        self.flag_dock.setObjectName('Flags')
        self.flag_widget = QtWidgets.QListWidget()
        if config['flags']:
            self.loadFlags({k: False for k in config['flags']})
        self.flag_dock.setWidget(self.flag_widget)
        self.flag_widget.itemChanged.connect(self.setDirty)

        self.labelList.itemActivated.connect(self.labelSelectionChanged)
        self.labelList.itemSelectionChanged.connect(self.labelSelectionChanged)
        self.labelList.itemDoubleClicked.connect(self.editLabelInline)
        # Connect to itemChanged to detect checkbox changes.
        self.labelList.itemChanged.connect(self.labelItemChanged)
        self.labelList.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked |
            QtWidgets.QAbstractItemView.EditKeyPressed |
            QtWidgets.QAbstractItemView.SelectedClicked
        )
        self.labelList.setDragDropMode(
            QtWidgets.QAbstractItemView.InternalMove)
        self.labelList.setParent(self)
        self.shape_dock = QtWidgets.QDockWidget('Polygon Labels', self)
        self.shape_dock.setObjectName('Labels')
        self.shape_dock.setWidget(self.labelList)

        self.uniqLabelList = EscapableQListWidget()
        self.uniqLabelList.setToolTip(
            "Select label to start annotating for it. "
            "Press 'Esc' to deselect.")
        self.uniqLabelList.itemChanged.connect(self.uniqLabelItemChanged)
        self.uniqLabelList.itemDoubleClicked.connect(self.editGlobalLabelInline)
        self.uniqLabelList.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked |
            QtWidgets.QAbstractItemView.EditKeyPressed |
            QtWidgets.QAbstractItemView.SelectedClicked
        )
        if self._config['labels']:
            for label in self._config['labels']:
                self.addUniqLabelItem(label)
        self.label_dock = QtWidgets.QDockWidget(u'Label List', self)
        self.label_dock.setObjectName(u'Label List')
        self.label_dock.setWidget(self.uniqLabelList)

        self.fileSearch = QtWidgets.QLineEdit()
        self.fileSearch.setPlaceholderText('Search Filename')
        self.fileSearch.textChanged.connect(self.fileSearchChanged)
        self.fileListWidget = QtWidgets.QListWidget()
        self.fileListWidget.itemSelectionChanged.connect(
            self.fileSelectionChanged
        )
        self.fileProgressLabel = QtWidgets.QLabel('已完成: 0 | 剩余: 0')
        fileListLayout = QtWidgets.QVBoxLayout()
        fileListLayout.setContentsMargins(0, 0, 0, 0)
        fileListLayout.setSpacing(0)
        fileListLayout.addWidget(self.fileSearch)
        fileListLayout.addWidget(self.fileListWidget)
        fileListLayout.addWidget(self.fileProgressLabel)
        self.file_dock = QtWidgets.QDockWidget(u'File List', self)
        self.file_dock.setObjectName(u'Files')
        fileListWidget = QtWidgets.QWidget()
        fileListWidget.setLayout(fileListLayout)
        self.file_dock.setWidget(fileListWidget)

        self.statsTable = QtWidgets.QTableWidget(0, 3)
        self.statsTable.setHorizontalHeaderLabels(
            ['Label', 'Polygons', 'Files']
        )
        self.statsTable.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.Stretch
        )
        self.statsTable.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        self.statsTable.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows
        )
        self.statsTable.itemDoubleClicked.connect(self.selectStatsLabel)
        self.stats_dock = QtWidgets.QDockWidget('Label Stats', self)
        self.stats_dock.setObjectName('Label Stats')
        self.stats_dock.setWidget(self.statsTable)

        self.zoomWidget = ZoomWidget()
        self.colorDialog = ColorDialog(parent=self)

        self.canvas = self.labelList.canvas = Canvas(
            epsilon=self._config['epsilon'],
        )
        self.canvas.zoomRequest.connect(self.zoomRequest)

        scrollArea = QtWidgets.QScrollArea()
        scrollArea.setWidget(self.canvas)
        scrollArea.setWidgetResizable(True)
        self.scrollBars = {
            Qt.Vertical: scrollArea.verticalScrollBar(),
            Qt.Horizontal: scrollArea.horizontalScrollBar(),
        }
        self.canvas.scrollRequest.connect(self.scrollRequest)

        self.canvas.newShape.connect(self.newShape)
        self.canvas.shapeMoved.connect(self.setDirty)
        self.canvas.selectionChanged.connect(self.shapeSelectionChanged)
        self.canvas.drawingPolygon.connect(self.toggleDrawingSensitive)

        self.setCentralWidget(scrollArea)

        features = QtWidgets.QDockWidget.DockWidgetFeatures()
        for dock in ['flag_dock', 'label_dock', 'shape_dock', 'file_dock']:
            if self._config[dock]['closable']:
                features = features | QtWidgets.QDockWidget.DockWidgetClosable
            if self._config[dock]['floatable']:
                features = features | QtWidgets.QDockWidget.DockWidgetFloatable
            if self._config[dock]['movable']:
                features = features | QtWidgets.QDockWidget.DockWidgetMovable
            getattr(self, dock).setFeatures(features)
            if self._config[dock]['show'] is False:
                getattr(self, dock).setVisible(False)
        self.stats_dock.setFeatures(features)

        self.addDockWidget(Qt.RightDockWidgetArea, self.flag_dock)
        self.addDockWidget(Qt.RightDockWidgetArea, self.label_dock)
        self.addDockWidget(Qt.RightDockWidgetArea, self.shape_dock)
        self.addDockWidget(Qt.RightDockWidgetArea, self.file_dock)
        self.addDockWidget(Qt.RightDockWidgetArea, self.stats_dock)

        # Actions
        action = functools.partial(utils.newAction, self)
        shortcuts = self._config['shortcuts']
        quit = action('&Quit', self.close, shortcuts['quit'], 'quit',
                      'Quit application')
        open_ = action('&Open', self.openFile, shortcuts['open'], 'open',
                       'Open image or label file')
        opendir = action('&Open Dir', self.openDirDialog,
                         shortcuts['open_dir'], 'open', u'Open Dir')
        openNextImg = action(
            '&Next Image',
            self.openNextImg,
            shortcuts['open_next'],
            'next',
            u'Open next (hold Ctl+Shift to copy labels)',
            enabled=False,
        )
        openPrevImg = action(
            '&Prev Image',
            self.openPrevImg,
            shortcuts['open_prev'],
            'prev',
            u'Open prev (hold Ctl+Shift to copy labels)',
            enabled=False,
        )
        save = action('&Save', self.saveFile, shortcuts['save'], 'save',
                      'Save labels to file', enabled=False)
        saveAs = action('&Save As', self.saveFileAs, shortcuts['save_as'],
                        'save-as', 'Save labels to a different file',
                        enabled=False)

        deleteFile = action(
            '&Delete File',
            self.deleteFile,
            shortcuts['delete_file'],
            'delete',
            'Delete current label file',
            enabled=False)

        changeOutputDir = action(
            '&Change Output Dir',
            slot=self.changeOutputDirDialog,
            shortcut=shortcuts['save_to'],
            icon='open',
            tip=u'Change where annotations are loaded/saved'
        )

        saveAuto = action(
            text='Save &Automatically',
            slot=lambda x: self.actions.saveAuto.setChecked(x),
            icon='save',
            tip='Save automatically',
            checkable=True,
            enabled=True,
        )
        saveAuto.setChecked(self._config['auto_save'])

        close = action('&Close', self.closeFile, shortcuts['close'], 'close',
                       'Close current file')
        color1 = action('Polygon &Line Color', self.chooseColor1,
                        shortcuts['edit_line_color'], 'color_line',
                        'Choose polygon line color')
        color2 = action('Polygon &Fill Color', self.chooseColor2,
                        shortcuts['edit_fill_color'], 'color',
                        'Choose polygon fill color')

        toggle_keep_prev_mode = action(
            'Keep Previous Annotation',
            self.toggleKeepPrevMode,
            shortcuts['toggle_keep_prev_mode'], None,
            'Toggle "keep pevious annotation" mode',
            checkable=True)
        toggle_keep_prev_mode.setChecked(self._config['keep_prev'])

        createMode = action(
            'Create Polygons',
            lambda: self.toggleDrawMode(False, createMode='polygon'),
            shortcuts['create_polygon'],
            'objects',
            'Start drawing polygons',
            enabled=False,
        )
        createRectangleMode = action(
            'Create Rectangle',
            lambda: self.toggleDrawMode(False, createMode='rectangle'),
            shortcuts['create_rectangle'],
            'objects',
            'Start drawing rectangles',
            enabled=False,
        )
        createCircleMode = action(
            'Create Circle',
            lambda: self.toggleDrawMode(False, createMode='circle'),
            shortcuts['create_circle'],
            'objects',
            'Start drawing circles',
            enabled=False,
        )
        createLineMode = action(
            'Create Line',
            lambda: self.toggleDrawMode(False, createMode='line'),
            shortcuts['create_line'],
            'objects',
            'Start drawing lines',
            enabled=False,
        )
        createPointMode = action(
            'Create Point',
            lambda: self.toggleDrawMode(False, createMode='point'),
            shortcuts['create_point'],
            'objects',
            'Start drawing points',
            enabled=False,
        )
        createLineStripMode = action(
            'Create LineStrip',
            lambda: self.toggleDrawMode(False, createMode='linestrip'),
            shortcuts['create_linestrip'],
            'objects',
            'Start drawing linestrip. Ctrl+LeftClick ends creation.',
            enabled=False,
        )
        editMode = action('Edit Polygons', self.setEditMode,
                          shortcuts['edit_polygon'], 'edit',
                          'Move and edit the selected polygons', enabled=False)

        delete = action('Delete Polygons', self.deleteSelectedShapeOrPoint,
                        shortcuts['delete_polygon'], 'cancel',
                        'Delete the selected polygons', enabled=False)
        deletePoint = action(
            'Delete Point',
            self.deleteSelectedPoint,
            None,
            'cancel',
            'Delete the selected point',
            enabled=False,
        )
        copy = action('Duplicate Polygons', self.copySelectedShape,
                      shortcuts['duplicate_polygon'], 'copy',
                      'Create a duplicate of the selected polygons',
                      enabled=False)
        copyToClipboard = action(
            'Copy Shapes',
            self.copyShapesToClipboard,
            'Ctrl+C',
            'copy',
            'Copy selected shapes to clipboard',
            enabled=False,
        )
        pasteFromClipboard = action(
            'Paste Shapes',
            self.pasteShapesFromClipboard,
            'Ctrl+V',
            'paste',
            'Paste copied shapes',
            enabled=False,
        )
        undoLastPoint = action('Undo last point', self.canvas.undoLastPoint,
                               shortcuts['undo_last_point'], 'undo',
                               'Undo last drawn point', enabled=False)
        addPoint = action('Add Point to Edge', self.canvas.addPointToEdge,
                          None, 'edit', 'Add point to the nearest edge',
                          enabled=False)
        referenceGuides = action(
            'Reference Guides',
            self.canvas.toggleReferenceGuides,
            'Ctrl+E',
            'edit',
            'Toggle horizontal and vertical guide lines',
            checkable=True,
            enabled=False,
        )
        referenceGuideColor = action(
            'Reference Guide Color',
            self.chooseReferenceGuideColor,
            None,
            'color',
            'Change reference guide color',
            enabled=False,
        )

        undo = action('Undo', self.undoShapeEdit, shortcuts['undo'], 'undo',
                      'Undo last add and edit of shape', enabled=False)

        hideAll = action('&Hide\nPolygons',
                         functools.partial(self.togglePolygons, False),
                         icon='eye', tip='Hide all polygons', enabled=False)
        showAll = action('&Show\nPolygons',
                         functools.partial(self.togglePolygons, True),
                         icon='eye', tip='Show all polygons', enabled=False)

        help = action('&Tutorial', self.tutorial, icon='help',
                      tip='Show tutorial page')

        zoom = QtWidgets.QWidgetAction(self)
        zoom.setDefaultWidget(self.zoomWidget)
        self.zoomWidget.setWhatsThis(
            'Zoom in or out of the image. Also accessible with '
            '{} and {} from the canvas.'
            .format(
                utils.fmtShortcut(
                    '{},{}'.format(
                        shortcuts['zoom_in'], shortcuts['zoom_out']
                    )
                ),
                utils.fmtShortcut("Ctrl+Wheel"),
            )
        )
        self.zoomWidget.setEnabled(False)

        zoomIn = action('Zoom &In', functools.partial(self.addZoom, 1.1),
                        shortcuts['zoom_in'], 'zoom-in',
                        'Increase zoom level', enabled=False)
        zoomOut = action('&Zoom Out', functools.partial(self.addZoom, 0.9),
                         shortcuts['zoom_out'], 'zoom-out',
                         'Decrease zoom level', enabled=False)
        zoomOrg = action('&Original size',
                         functools.partial(self.setZoom, 100),
                         shortcuts['zoom_to_original'], 'zoom',
                         'Zoom to original size', enabled=False)
        fitWindow = action('&Fit Window', self.setFitWindow,
                           shortcuts['fit_window'], 'fit-window',
                           'Zoom follows window size', checkable=True,
                           enabled=False)
        fitWidth = action('Fit &Width', self.setFitWidth,
                          shortcuts['fit_width'], 'fit-width',
                          'Zoom follows window width',
                          checkable=True, enabled=False)
        # Group zoom controls into a list for easier toggling.
        zoomActions = (self.zoomWidget, zoomIn, zoomOut, zoomOrg,
                       fitWindow, fitWidth)
        self.zoomMode = self.FIT_WINDOW
        fitWindow.setChecked(Qt.Checked)
        self.scalers = {
            self.FIT_WINDOW: self.scaleFitWindow,
            self.FIT_WIDTH: self.scaleFitWidth,
            # Set to one to scale to 100% when loading files.
            self.MANUAL_ZOOM: lambda: 1,
        }

        edit = action('&Edit Label', self.editLabel, 'Ctrl+Shift+E',
                      'edit', 'Modify the label of the selected polygon',
                      enabled=False)
        batchRename = action(
            'Batch Rename Labels',
            self.batchRenameLabels,
            'Ctrl+Alt+E',
            'edit',
            'Rename all selected polygons to the same label',
            enabled=False,
        )
        batchConvert = action(
            'Batch Convert Labels',
            self.openBatchLabelMappingDialog,
            'Ctrl+Alt+M',
            'edit',
            'Apply multiple label mappings across the current project',
            enabled=True,
        )
        rollbackLast = action(
            'Rollback Last Bulk Operation',
            self.rollbackLastBulkOperation,
            'Ctrl+Alt+Z',
            'undo',
            'Restore files from the latest bulk operation backup',
            enabled=True,
        )
        refreshStats = action(
            'Refresh Label Stats',
            self.refreshLabelStatsPanel,
            'Ctrl+Alt+R',
            'labels',
            'Rescan current project and refresh label statistics',
            enabled=True,
        )
        lockShapeMove = action(
            'Lock Shape Move',
            self.canvas.setShapeMoveLocked,
            'Ctrl+Alt+L',
            'edit',
            'Prevent polygon body drag unless explicitly unlocked',
            checkable=True,
            enabled=True,
        )
        lockShapeMove.setChecked(True)

        shapeLineColor = action(
            'Shape &Line Color', self.chshapeLineColor, icon='color-line',
            tip='Change the line color for this specific shape', enabled=False)
        shapeFillColor = action(
            'Shape &Fill Color', self.chshapeFillColor, icon='color',
            tip='Change the fill color for this specific shape', enabled=False)
        fill_drawing = action(
            'Fill Drawing Polygon',
            lambda x: self.canvas.setFillDrawing(x),
            None,
            'color',
            'Fill polygon while drawing',
            checkable=True,
            enabled=True,
        )
        fill_drawing.setChecked(True)

        # Lavel list context menu.
        labelMenu = QtWidgets.QMenu()
        utils.addActions(labelMenu, (edit, batchRename, delete))
        self.labelList.setContextMenuPolicy(Qt.CustomContextMenu)
        self.labelList.customContextMenuRequested.connect(
            self.popLabelListMenu)

        # Store actions for further handling.
        self.actions = utils.struct(
            saveAuto=saveAuto,
            changeOutputDir=changeOutputDir,
            save=save, saveAs=saveAs, open=open_, close=close,
            deleteFile=deleteFile,
            lineColor=color1, fillColor=color2,
            toggleKeepPrevMode=toggle_keep_prev_mode,
            delete=delete, edit=edit, batchRename=batchRename,
            batchConvert=batchConvert, rollbackLast=rollbackLast,
            refreshStats=refreshStats, lockShapeMove=lockShapeMove, copy=copy,
            copyToClipboard=copyToClipboard,
            pasteFromClipboard=pasteFromClipboard,
            deletePoint=deletePoint,
            undoLastPoint=undoLastPoint, undo=undo,
            addPoint=addPoint,
            referenceGuides=referenceGuides,
            referenceGuideColor=referenceGuideColor,
            createMode=createMode, editMode=editMode,
            createRectangleMode=createRectangleMode,
            createCircleMode=createCircleMode,
            createLineMode=createLineMode,
            createPointMode=createPointMode,
            createLineStripMode=createLineStripMode,
            shapeLineColor=shapeLineColor, shapeFillColor=shapeFillColor,
            zoom=zoom, zoomIn=zoomIn, zoomOut=zoomOut, zoomOrg=zoomOrg,
            fitWindow=fitWindow, fitWidth=fitWidth,
            zoomActions=zoomActions,
            openNextImg=openNextImg, openPrevImg=openPrevImg,
            fileMenuActions=(open_, opendir, save, saveAs, close, quit),
            tool=(),
            editMenu=(edit, batchRename, batchConvert, rollbackLast, None,
                      copy, copyToClipboard, pasteFromClipboard,
                      delete, deletePoint, None, undo, undoLastPoint,
                      referenceGuides, referenceGuideColor, lockShapeMove,
                      refreshStats,
                      None, color1, color2, None, toggle_keep_prev_mode),
            # menu shown at right click
            menu=(
                createMode,
                createRectangleMode,
                createCircleMode,
                createLineMode,
                createPointMode,
                createLineStripMode,
                editMode,
                edit,
                copy,
                copyToClipboard,
                pasteFromClipboard,
                delete,
                deletePoint,
                shapeLineColor,
                shapeFillColor,
                undo,
                undoLastPoint,
                addPoint,
                batchConvert,
                rollbackLast,
                lockShapeMove,
                referenceGuides,
                referenceGuideColor,
            ),
            onLoadActive=(
                close,
                createMode,
                createRectangleMode,
                createCircleMode,
                createLineMode,
                createPointMode,
                createLineStripMode,
                editMode,
                referenceGuides,
                referenceGuideColor,
                deletePoint,
            ),
            onShapesPresent=(saveAs, hideAll, showAll),
        )

        self.canvas.edgeSelected.connect(self.actions.addPoint.setEnabled)
        self.canvas.vertexSelected.connect(self.actions.deletePoint.setEnabled)

        self.menus = utils.struct(
            file=self.menu('&File'),
            edit=self.menu('&Edit'),
            view=self.menu('&View'),
            help=self.menu('&Help'),
            recentFiles=QtWidgets.QMenu('Open &Recent'),
            labelList=labelMenu,
        )

        utils.addActions(
            self.menus.file,
            (
                open_,
                openNextImg,
                openPrevImg,
                opendir,
                self.menus.recentFiles,
                save,
                saveAs,
                saveAuto,
                changeOutputDir,
                close,
                deleteFile,
                None,
                quit,
            ),
        )
        utils.addActions(self.menus.help, (help,))
        utils.addActions(
            self.menus.view,
            (
                self.flag_dock.toggleViewAction(),
                self.label_dock.toggleViewAction(),
                self.shape_dock.toggleViewAction(),
                self.file_dock.toggleViewAction(),
                self.stats_dock.toggleViewAction(),
                None,
                fill_drawing,
                lockShapeMove,
                refreshStats,
                None,
                hideAll,
                showAll,
                None,
                zoomIn,
                zoomOut,
                zoomOrg,
                None,
                fitWindow,
                fitWidth,
                None,
            ),
        )

        self.menus.file.aboutToShow.connect(self.updateFileMenu)

        # Custom context menu for the canvas widget:
        utils.addActions(self.canvas.menus[0], self.actions.menu)
        utils.addActions(
            self.canvas.menus[1],
            (
                action('&Copy here', self.copyShape),
                action('&Move here', self.moveShape),
                None,
                copyToClipboard,
                pasteFromClipboard,
            ),
        )

        self.tools = self.toolbar('Tools')
        # Menu buttons on Left
        self.actions.tool = (
            open_,
            opendir,
            openNextImg,
            openPrevImg,
            save,
            deleteFile,
            None,
            createMode,
            editMode,
            referenceGuides,
            referenceGuideColor,
            lockShapeMove,
            refreshStats,
            copy,
            copyToClipboard,
            pasteFromClipboard,
            delete,
            deletePoint,
            undo,
            None,
            zoomIn,
            zoom,
            zoomOut,
            fitWindow,
            fitWidth,
        )

        self.statusBar().showMessage('%s started.' % __appname__)
        self.statusBar().show()

        if output_file is not None and self._config['auto_save']:
            logger.warn(
                'If `auto_save` argument is True, `output_file` argument '
                'is ignored and output filename is automatically '
                'set as IMAGE_BASENAME.json.'
            )
        self.output_file = output_file
        self.output_dir = output_dir

        # Application state.
        self.image = QtGui.QImage()
        self.imagePath = None
        self.recentFiles = []
        self.maxRecent = 7
        self.lineColor = None
        self.fillColor = None
        self.otherData = None
        self.zoom_level = 100
        self.fit_window = False
        self._copiedShapes = []
        self._pasteCount = 0

        if filename is not None and osp.isdir(filename):
            self.importDirImages(filename, load=False)
        else:
            self.filename = filename

        if config['file_search']:
            self.fileSearch.setText(config['file_search'])
            self.fileSearchChanged()

        # XXX: Could be completely declarative.
        # Restore application settings.
        self.settings = QtCore.QSettings('labelme', 'labelme')
        # FIXME: QSettings.value can return None on PyQt4
        self.recentFiles = self.settings.value('recentFiles', []) or []
        size = self.settings.value('window/size', QtCore.QSize(600, 500))
        position = self.settings.value('window/position', QtCore.QPoint(0, 0))
        self.resize(size)
        self.move(position)
        # or simply:
        # self.restoreGeometry(settings['window/geometry']
        self.restoreState(
            self.settings.value('window/state', QtCore.QByteArray()))
        self.lineColor = QtGui.QColor(
            self.settings.value('line/color', Shape.line_color))
        self.fillColor = QtGui.QColor(
            self.settings.value('fill/color', Shape.fill_color))
        self.referenceGuideColor = QtGui.QColor(
            self.settings.value(
                'reference_guide/color', QtGui.QColor(255, 80, 120, 150)))
        Shape.line_color = self.lineColor
        Shape.fill_color = self.fillColor
        self.canvas.setReferenceGuideColor(self.referenceGuideColor)

        # Populate the File menu dynamically.
        self.updateFileMenu()
        # Since loading the file may take some time,
        # make sure it runs in the background.
        if self.filename is not None:
            self.queueEvent(functools.partial(self.loadFile, self.filename))

        # Callbacks:
        self.zoomWidget.valueChanged.connect(self.paintCanvas)

        self.populateModeActions()

        # self.firstStart = True
        # if self.firstStart:
        #    QWhatsThis.enterWhatsThisMode()

    def menu(self, title, actions=None):
        menu = self.menuBar().addMenu(title)
        if actions:
            utils.addActions(menu, actions)
        return menu

    def toolbar(self, title, actions=None):
        toolbar = ToolBar(title)
        toolbar.setObjectName('%sToolBar' % title)
        # toolbar.setOrientation(Qt.Vertical)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        if actions:
            utils.addActions(toolbar, actions)
        self.addToolBar(Qt.LeftToolBarArea, toolbar)
        return toolbar

    # Support Functions

    def noShapes(self):
        return not self.labelList.itemsToShapes

    def populateModeActions(self):
        tool, menu = self.actions.tool, self.actions.menu
        self.tools.clear()
        utils.addActions(self.tools, tool)
        self.canvas.menus[0].clear()
        utils.addActions(self.canvas.menus[0], menu)
        self.menus.edit.clear()
        actions = (
            self.actions.createMode,
            self.actions.createRectangleMode,
            self.actions.createCircleMode,
            self.actions.createLineMode,
            self.actions.createPointMode,
            self.actions.createLineStripMode,
            self.actions.editMode,
        )
        utils.addActions(self.menus.edit, actions + self.actions.editMenu)

    def setDirty(self):
        if self._config['auto_save'] or self.actions.saveAuto.isChecked():
            label_file = osp.splitext(self.imagePath)[0] + '.json'
            if self.output_dir:
                label_file_without_path = osp.basename(label_file)
                label_file = osp.join(self.output_dir, label_file_without_path)
            self.saveLabels(label_file)
            return
        self.dirty = True
        self.actions.save.setEnabled(True)
        self.actions.undo.setEnabled(self.canvas.isShapeRestorable)
        title = __appname__
        if self.filename is not None:
            title = '{} - {}*'.format(title, self.filename)
        self.setWindowTitle(title)

    def setClean(self):
        self.dirty = False
        self.actions.save.setEnabled(False)
        self.actions.createMode.setEnabled(True)
        self.actions.createRectangleMode.setEnabled(True)
        self.actions.createCircleMode.setEnabled(True)
        self.actions.createLineMode.setEnabled(True)
        self.actions.createPointMode.setEnabled(True)
        self.actions.createLineStripMode.setEnabled(True)
        title = __appname__
        if self.filename is not None:
            title = '{} - {}'.format(title, self.filename)
        self.setWindowTitle(title)

        if self.hasLabelFile():
            self.actions.deleteFile.setEnabled(True)
        else:
            self.actions.deleteFile.setEnabled(False)

    def toggleActions(self, value=True):
        """Enable/Disable widgets which depend on an opened image."""
        for z in self.actions.zoomActions:
            z.setEnabled(value)
        for action in self.actions.onLoadActive:
            action.setEnabled(value)
        if not value:
            self.actions.copyToClipboard.setEnabled(False)
            self.actions.pasteFromClipboard.setEnabled(False)
        else:
            self.actions.copyToClipboard.setEnabled(len(self.canvas.selectedShapes))
            self.actions.pasteFromClipboard.setEnabled(bool(self._copiedShapes))

    def queueEvent(self, function):
        QtCore.QTimer.singleShot(0, function)

    def status(self, message, delay=5000):
        self.statusBar().showMessage(message, delay)

    def resetState(self):
        self.labelList.clear()
        self.filename = None
        self.imagePath = None
        self.imageData = None
        self.labelFile = None
        self.otherData = None
        self.canvas.resetState()

    def currentItem(self):
        items = self.labelList.selectedItems()
        if items:
            return items[0]
        return None

    def addRecentFile(self, filename):
        if filename in self.recentFiles:
            self.recentFiles.remove(filename)
        elif len(self.recentFiles) >= self.maxRecent:
            self.recentFiles.pop()
        self.recentFiles.insert(0, filename)

    # Callbacks

    def undoShapeEdit(self):
        self.canvas.restoreShape()
        self.labelList.clear()
        self.loadShapes(self.canvas.shapes)
        self.actions.undo.setEnabled(self.canvas.isShapeRestorable)

    def tutorial(self):
        url = 'https://github.com/wkentaro/labelme/tree/master/examples/tutorial'  # NOQA
        webbrowser.open(url)

    def toggleAddPointEnabled(self, enabled):
        self.actions.addPoint.setEnabled(enabled)

    def toggleDrawingSensitive(self, drawing=True):
        """Toggle drawing sensitive.

        In the middle of drawing, toggling between modes should be disabled.
        """
        self.actions.editMode.setEnabled(not drawing)
        self.actions.undoLastPoint.setEnabled(drawing)
        self.actions.undo.setEnabled(not drawing)
        self.actions.delete.setEnabled(not drawing)

    def toggleDrawMode(self, edit=True, createMode='polygon'):
        self.canvas.setEditing(edit)
        self.canvas.createMode = createMode
        if edit:
            self.actions.createMode.setEnabled(True)
            self.actions.createRectangleMode.setEnabled(True)
            self.actions.createCircleMode.setEnabled(True)
            self.actions.createLineMode.setEnabled(True)
            self.actions.createPointMode.setEnabled(True)
            self.actions.createLineStripMode.setEnabled(True)
        else:
            if createMode == 'polygon':
                self.actions.createMode.setEnabled(False)
                self.actions.createRectangleMode.setEnabled(True)
                self.actions.createCircleMode.setEnabled(True)
                self.actions.createLineMode.setEnabled(True)
                self.actions.createPointMode.setEnabled(True)
                self.actions.createLineStripMode.setEnabled(True)
            elif createMode == 'rectangle':
                self.actions.createMode.setEnabled(True)
                self.actions.createRectangleMode.setEnabled(False)
                self.actions.createCircleMode.setEnabled(True)
                self.actions.createLineMode.setEnabled(True)
                self.actions.createPointMode.setEnabled(True)
                self.actions.createLineStripMode.setEnabled(True)
            elif createMode == 'line':
                self.actions.createMode.setEnabled(True)
                self.actions.createRectangleMode.setEnabled(True)
                self.actions.createCircleMode.setEnabled(True)
                self.actions.createLineMode.setEnabled(False)
                self.actions.createPointMode.setEnabled(True)
                self.actions.createLineStripMode.setEnabled(True)
            elif createMode == 'point':
                self.actions.createMode.setEnabled(True)
                self.actions.createRectangleMode.setEnabled(True)
                self.actions.createCircleMode.setEnabled(True)
                self.actions.createLineMode.setEnabled(True)
                self.actions.createPointMode.setEnabled(False)
                self.actions.createLineStripMode.setEnabled(True)
            elif createMode == "circle":
                self.actions.createMode.setEnabled(True)
                self.actions.createRectangleMode.setEnabled(True)
                self.actions.createCircleMode.setEnabled(False)
                self.actions.createLineMode.setEnabled(True)
                self.actions.createPointMode.setEnabled(True)
                self.actions.createLineStripMode.setEnabled(True)
            elif createMode == "linestrip":
                self.actions.createMode.setEnabled(True)
                self.actions.createRectangleMode.setEnabled(True)
                self.actions.createCircleMode.setEnabled(True)
                self.actions.createLineMode.setEnabled(True)
                self.actions.createPointMode.setEnabled(True)
                self.actions.createLineStripMode.setEnabled(False)
            else:
                raise ValueError('Unsupported createMode: %s' % createMode)
        self.actions.editMode.setEnabled(not edit)

    def setEditMode(self):
        self.toggleDrawMode(True)

    def updateFileMenu(self):
        current = self.filename

        def exists(filename):
            return osp.exists(str(filename))

        menu = self.menus.recentFiles
        menu.clear()
        files = [f for f in self.recentFiles if f != current and exists(f)]
        for i, f in enumerate(files):
            icon = utils.newIcon('labels')
            action = QtWidgets.QAction(
                icon, '&%d %s' % (i + 1, QtCore.QFileInfo(f).fileName()), self)
            action.triggered.connect(functools.partial(self.loadRecent, f))
            menu.addAction(action)

    def popLabelListMenu(self, point):
        self.menus.labelList.exec_(self.labelList.mapToGlobal(point))

    def addUniqLabelItem(self, text):
        if self.uniqLabelList.findItems(text, Qt.MatchExactly):
            return
        item = QtWidgets.QListWidgetItem(text)
        item.setFlags(
            item.flags() | Qt.ItemIsEditable | Qt.ItemIsEnabled |
            Qt.ItemIsSelectable
        )
        item.setData(Qt.UserRole, text)
        self.uniqLabelList.addItem(item)
        self.uniqLabelList.sortItems()

    def dedupeUniqLabelList(self):
        seen = set()
        for i in range(self.uniqLabelList.count() - 1, -1, -1):
            item = self.uniqLabelList.item(i)
            text = item.text()
            if text in seen:
                self.uniqLabelList.takeItem(i)
            else:
                seen.add(text)

    def getAnnotationFilePath(self, filename):
        if filename.lower().endswith('.json'):
            label_file = filename
        else:
            label_file = osp.splitext(filename)[0] + '.json'
        if self.output_dir:
            label_file = osp.join(self.output_dir, osp.basename(label_file))
        return label_file

    def refreshFileListChecks(self):
        for i in range(self.fileListWidget.count()):
            item = self.fileListWidget.item(i)
            filename = item.data(Qt.UserRole)
            if not filename:
                continue
            label_file = self.getAnnotationFilePath(str(filename))
            item.setCheckState(
                Qt.Checked
                if osp.exists(label_file) and LabelFile.is_label_file(label_file)
                else Qt.Unchecked
            )
        self.updateFileProgress()

    def projectRootDir(self):
        return self.output_dir or self.lastOpenDir or self.currentPath()

    def historyRootDir(self):
        return osp.join(self.projectRootDir(), '.labelme_custom_history')

    def ensureHistoryRootDir(self):
        history_root = self.historyRootDir()
        if not osp.exists(history_root):
            os.makedirs(history_root)
        return history_root

    def currentAnnotationFilePath(self):
        if not self.filename:
            return None
        return self.getAnnotationFilePath(str(self.filename))

    def collectProjectLabelFiles(self):
        label_files = []
        seen = set()
        for image_file in self.imageList:
            label_file = self.getAnnotationFilePath(str(image_file))
            if not osp.exists(label_file) or not LabelFile.is_label_file(label_file):
                continue
            if label_file not in seen:
                seen.add(label_file)
                label_files.append(label_file)
        current_label_file = self.currentAnnotationFilePath()
        if (current_label_file and osp.exists(current_label_file) and
                current_label_file not in seen):
            label_files.append(current_label_file)
        return label_files

    def saveCurrentLabelFileIfNeeded(self):
        label_file = self.currentAnnotationFilePath()
        if not label_file or not self.filename:
            return True
        if not self.dirty:
            return True
        return self.saveLabels(label_file)

    def loadJsonWithFallbackEncoding(self, filename):
        encodings = []
        preferred = locale.getpreferredencoding(False)
        for encoding in ['utf-8', 'utf-8-sig', preferred, 'gb18030', 'gbk']:
            if encoding and encoding not in encodings:
                encodings.append(encoding)

        last_error = None
        for encoding in encodings:
            try:
                with open(filename, 'r', encoding=encoding) as f:
                    return json.load(f), encoding
            except Exception as e:
                last_error = e
        raise last_error

    def replaceLabelInJsonFile(self, filename, old_label, new_label):
        return self.applyLabelMappingToJsonFile(
            filename, {old_label: new_label}
        )

    def applyLabelMappingToJsonFile(self, filename, mapping):
        data, encoding = self.loadJsonWithFallbackEncoding(filename)

        changed = 0
        for shape in data.get('shapes', []):
            label = shape.get('label')
            if label in mapping and mapping[label] != label:
                shape['label'] = mapping[label]
                changed += 1

        if changed:
            with open(filename, 'w', encoding=encoding) as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        return changed

    def scanProjectLabelData(self, mapping=None, show_progress=False,
                             title='Scanning labels...'):
        label_files = self.collectProjectLabelFiles()
        stats = {}
        pair_stats = {}
        affected_files = []
        errors = []
        progress = None

        if show_progress:
            progress = QtWidgets.QProgressDialog(
                title, 'Cancel', 0, len(label_files), self
            )
            progress.setWindowTitle(title)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)

        for index, label_file in enumerate(label_files, 1):
            if progress:
                progress.setLabelText(
                    '{} ({}/{})'.format(
                        osp.basename(label_file), index, len(label_files)
                    )
                )
                progress.setValue(index - 1)
                QtWidgets.QApplication.processEvents()
                if progress.wasCanceled():
                    break

            try:
                data, _encoding = self.loadJsonWithFallbackEncoding(label_file)
                labels_in_file = set()
                affected_in_file = 0
                file_pairs = set()
                for shape in data.get('shapes', []):
                    label = shape.get('label')
                    if not label:
                        continue
                    labels_in_file.add(label)
                    record = stats.setdefault(
                        label, {'label': label, 'polygons': 0, 'files': 0}
                    )
                    record['polygons'] += 1

                    if mapping and label in mapping and mapping[label] != label:
                        pair = (label, mapping[label])
                        pair_record = pair_stats.setdefault(
                            pair,
                            {
                                'old_label': label,
                                'new_label': mapping[label],
                                'polygons': 0,
                                'files': 0,
                            }
                        )
                        pair_record['polygons'] += 1
                        affected_in_file += 1
                        file_pairs.add(pair)

                for label in labels_in_file:
                    stats[label]['files'] += 1
                if affected_in_file:
                    affected_files.append(label_file)
                    for pair in file_pairs:
                        pair_stats[pair]['files'] += 1
            except Exception as e:
                errors.append('{}: {}'.format(label_file, e))

        if progress:
            progress.setValue(len(label_files))

        stats_rows = sorted(
            stats.values(),
            key=lambda row: (-row['polygons'], row['label'].lower())
        )
        preview_rows = sorted(
            pair_stats.values(),
            key=lambda row: (-row['polygons'], row['old_label'].lower())
        )
        changed_polygons = sum(row['polygons'] for row in preview_rows)
        return {
            'stats_rows': stats_rows,
            'preview_rows': preview_rows,
            'affected_files': affected_files,
            'changed_files': len(affected_files),
            'changed_polygons': changed_polygons,
            'errors': errors,
            'canceled': bool(progress and progress.wasCanceled()),
        }

    def buildPreviewSummary(self, preview_rows, changed_polygons, changed_files):
        if not preview_rows:
            return 'No matching labels found in the current project.'
        return (
            'Will update {} polygon(s) in {} file(s) across {} mapping(s).'
            .format(changed_polygons, changed_files, len(preview_rows))
        )

    def previewLabelMappings(self, mapping):
        scan = self.scanProjectLabelData(
            mapping=mapping,
            show_progress=True,
            title='Preview Label Mapping'
        )
        if scan['errors']:
            self.errorMessage(
                'Preview Label Mapping Error',
                '<br/>'.join(scan['errors'][:5])
            )
        summary = self.buildPreviewSummary(
            scan['preview_rows'],
            scan['changed_polygons'],
            scan['changed_files'],
        )
        return scan['preview_rows'], summary

    def buildConfirmationMessage(self, preview_rows, summary):
        if not preview_rows:
            return summary
        preview_lines = [
            '{} -> {} : {} polygon(s) / {} file(s)'.format(
                row['old_label'], row['new_label'],
                row['polygons'], row['files']
            )
            for row in preview_rows[:8]
        ]
        if len(preview_rows) > 8:
            preview_lines.append('...')
        return summary + '<br/><br/>' + '<br/>'.join(preview_lines)

    def createBulkOperationDir(self, operation_name):
        history_root = self.ensureHistoryRootDir()
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        operation_dir = osp.join(history_root, '{}_{}'.format(timestamp, operation_name))
        os.makedirs(operation_dir)
        return operation_dir

    def backupFileForOperation(self, source_file, operation_dir):
        project_root = self.projectRootDir()
        try:
            relative_path = osp.relpath(source_file, project_root)
        except Exception:
            relative_path = osp.basename(source_file)
        backup_path = osp.join(operation_dir, 'backup', relative_path)
        backup_dir = osp.dirname(backup_path)
        if backup_dir and not osp.exists(backup_dir):
            os.makedirs(backup_dir)
        shutil.copy2(source_file, backup_path)
        return relative_path, backup_path

    def writeOperationManifest(self, operation_dir, manifest):
        manifest_path = osp.join(operation_dir, 'manifest.json')
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        return manifest_path

    def listOperationManifests(self):
        history_root = self.historyRootDir()
        if not osp.exists(history_root):
            return []
        manifests = []
        for name in sorted(os.listdir(history_root), reverse=True):
            manifest_path = osp.join(history_root, name, 'manifest.json')
            if osp.exists(manifest_path):
                try:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        manifest = json.load(f)
                    manifests.append((manifest_path, manifest))
                except Exception:
                    continue
        return manifests

    def applyLabelMappingAcrossProject(self, mapping, operation_name='mapping'):
        mapping = {
            old.strip(): new.strip()
            for old, new in mapping.items()
            if old and new and old.strip() and new.strip() and old.strip() != new.strip()
        }
        if not mapping:
            self.status('No label mapping changes to apply.')
            return False
        for new_label in mapping.values():
            if not self.validateLabel(new_label):
                self.errorMessage(
                    'Invalid label',
                    "Invalid label '{}' with validation type '{}'"
                    .format(new_label, self._config['validate_label'])
                )
                return False
        if not self.saveCurrentLabelFileIfNeeded():
            return False

        scan = self.scanProjectLabelData(
            mapping=mapping,
            show_progress=True,
            title='Preview Label Mapping'
        )
        if scan['errors']:
            self.errorMessage(
                'Preview Label Mapping Error',
                '<br/>'.join(scan['errors'][:5])
            )
            return False
        if scan['canceled']:
            return False
        if not scan['preview_rows']:
            self.status('No matching labels found in the current project.')
            return False

        summary = self.buildPreviewSummary(
            scan['preview_rows'],
            scan['changed_polygons'],
            scan['changed_files'],
        )
        mb = QtWidgets.QMessageBox
        if mb.question(
            self,
            'Apply Label Mapping',
            self.buildConfirmationMessage(scan['preview_rows'], summary),
            mb.Yes | mb.No,
        ) != mb.Yes:
            return False

        operation_dir = self.createBulkOperationDir(operation_name)
        progress = QtWidgets.QProgressDialog(
            'Applying label mapping...',
            'Cancel',
            0,
            len(scan['affected_files']),
            self,
        )
        progress.setWindowTitle('Apply Label Mapping')
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        manifest = {
            'created_at': datetime.datetime.now().isoformat(),
            'operation_name': operation_name,
            'mapping': mapping,
            'preview_rows': scan['preview_rows'],
            'changed_polygons': scan['changed_polygons'],
            'changed_files': [],
            'rolled_back': False,
        }
        applied_files = []
        errors = []

        for index, label_file in enumerate(scan['affected_files'], 1):
            progress.setLabelText(
                'Updating {} ({}/{})'.format(
                    osp.basename(label_file), index, len(scan['affected_files'])
                )
            )
            progress.setValue(index - 1)
            QtWidgets.QApplication.processEvents()
            if progress.wasCanceled():
                break

            try:
                relative_path, backup_path = self.backupFileForOperation(
                    label_file, operation_dir
                )
                changed = self.applyLabelMappingToJsonFile(label_file, mapping)
                if changed:
                    manifest['changed_files'].append(
                        {
                            'file': label_file,
                            'relative_path': relative_path,
                            'backup_file': backup_path,
                            'changed_polygons': changed,
                        }
                    )
                    applied_files.append(label_file)
            except Exception as e:
                errors.append('{}: {}'.format(label_file, e))

        progress.setValue(len(scan['affected_files']))

        if progress.wasCanceled() or errors:
            for changed_file in reversed(manifest['changed_files']):
                shutil.copy2(changed_file['backup_file'], changed_file['file'])
            if errors:
                self.errorMessage(
                    'Apply Label Mapping Error',
                    '<br/>'.join(errors[:5])
                )
            else:
                self.status('Bulk operation canceled. Changes were rolled back.')
            return False

        self.writeOperationManifest(operation_dir, manifest)

        for new_label in mapping.values():
            self.addUniqLabelItem(new_label)
            self.labelDialog.addLabelHistory(new_label)

        current_label_file = self.currentAnnotationFilePath()
        if current_label_file and current_label_file in applied_files and self.filename:
            self.loadFile(self.filename)
        self.refreshFileListChecks()
        self.refreshLabelStatsPanel()

        self.status(
            'Applied label mapping to {} polygon(s) in {} file(s).'
            .format(scan['changed_polygons'], len(manifest['changed_files']))
        )
        return True

    def rollbackLastBulkOperation(self):
        manifests = self.listOperationManifests()
        latest = None
        manifest_path = None
        for path, manifest in manifests:
            if not manifest.get('rolled_back'):
                manifest_path = path
                latest = manifest
                break
        if latest is None:
            self.status('No bulk operation available for rollback.')
            return
        if not self.mayContinue():
            return

        mb = QtWidgets.QMessageBox
        summary = 'Restore {} file(s) from the latest bulk operation backup?'.format(
            len(latest.get('changed_files', []))
        )
        if mb.question(self, 'Rollback Last Bulk Operation', summary,
                       mb.Yes | mb.No) != mb.Yes:
            return

        errors = []
        for changed_file in latest.get('changed_files', []):
            try:
                shutil.copy2(changed_file['backup_file'], changed_file['file'])
            except Exception as e:
                errors.append('{}: {}'.format(changed_file['file'], e))

        if errors:
            self.errorMessage(
                'Rollback Error',
                '<br/>'.join(errors[:5])
            )
            return

        latest['rolled_back'] = True
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(latest, f, ensure_ascii=False, indent=2)

        if self.filename:
            self.loadFile(self.filename)
        else:
            self.refreshFileListChecks()
            self.refreshLabelStatsPanel()
        self.status('Rolled back the latest bulk operation.')

    def refreshLabelStatsPanel(self, _value=False):
        scan = self.scanProjectLabelData(
            show_progress=bool(self.imageList),
            title='Refresh Label Stats'
        )
        self.statsTable.setRowCount(len(scan['stats_rows']))
        for row_index, row in enumerate(scan['stats_rows']):
            values = [
                row['label'],
                str(row['polygons']),
                str(row['files']),
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                self.statsTable.setItem(row_index, column, item)
        if scan['errors']:
            self.errorMessage(
                'Label Stats Error',
                '<br/>'.join(scan['errors'][:5])
            )

    def selectStatsLabel(self, item):
        if item is None:
            return
        label = self.statsTable.item(item.row(), 0).text()
        matches = self.uniqLabelList.findItems(label, Qt.MatchExactly)
        if matches:
            self.uniqLabelList.setCurrentItem(matches[0])
            self.uniqLabelList.scrollToItem(matches[0])

    def openBatchLabelMappingDialog(self):
        dialog = LabelMappingDialog(self.previewLabelMappings, self)
        if not dialog.exec_():
            return
        try:
            mapping = dialog.getMapping() or dialog.parseMappings()
        except Exception as e:
            self.errorMessage('Batch Label Mapping', str(e))
            return
        self.applyLabelMappingAcrossProject(mapping, operation_name='mapping')

    def syncLabelRenameAcrossProject(self, old_label, new_label):
        return self.applyLabelMappingAcrossProject(
            {old_label: new_label},
            operation_name='rename'
        )

    def syncLabelRenameAcrossProject(self, old_label, new_label):
        old_label = old_label.strip()
        new_label = new_label.strip()
        if not old_label or not new_label:
            return False
        if old_label == new_label:
            return True
        if not self.validateLabel(new_label):
            self.errorMessage(
                'Invalid label',
                "Invalid label '{}' with validation type '{}'"
                .format(new_label, self._config['validate_label'])
            )
            return False

        label_files = []
        for image_file in self.imageList:
            label_file = self.getAnnotationFilePath(str(image_file))
            if osp.exists(label_file) and LabelFile.is_label_file(label_file):
                label_files.append(label_file)

        current_label_file = None
        if self.filename:
            current_label_file = self.getAnnotationFilePath(str(self.filename))
            if current_label_file not in label_files:
                label_files.append(current_label_file)

        mb = QtWidgets.QMessageBox
        msg = (
            "Rename label '{}' to '{}' in all annotations of the current "
            "project?"
        ).format(old_label, new_label)
        if mb.question(self, 'Sync Label Rename', msg, mb.Yes | mb.No) != mb.Yes:
            return False

        progress = QtWidgets.QProgressDialog(
            "Updating label across project...",
            "Cancel",
            0,
            len(label_files),
            self,
        )
        progress.setWindowTitle('Sync Label Rename')
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        changed_files = 0
        changed_shapes = 0
        errors = []
        current_changed = 0

        for index, label_file in enumerate(label_files, 1):
            progress.setLabelText(
                'Updating {} ({}/{})'.format(
                    osp.basename(label_file), index, len(label_files)
                )
            )
            progress.setValue(index - 1)
            QtWidgets.QApplication.processEvents()
            if progress.wasCanceled():
                break

            try:
                if current_label_file and label_file == current_label_file:
                    current_changed = self.replaceLoadedShapesLabel(
                        old_label, new_label
                    )
                    if current_changed:
                        if not self.saveLabels(label_file):
                            raise LabelFileError(
                                'failed to save current label file'
                            )
                        changed_files += 1
                        changed_shapes += current_changed
                elif osp.exists(label_file):
                    changed = self.replaceLabelInJsonFile(
                        label_file, old_label, new_label
                    )
                    if changed:
                        changed_files += 1
                        changed_shapes += changed
            except Exception as e:
                errors.append('{}: {}'.format(label_file, e))

        progress.setValue(len(label_files))

        if current_changed:
            self.setClean()
        self.labelDialog.addLabelHistory(new_label)
        self.addUniqLabelItem(new_label)
        self.refreshFileListChecks()

        if errors:
            self.errorMessage(
                'Sync Label Rename Error',
                '<br/>'.join(errors[:5])
            )
            return False

        if progress.wasCanceled():
            self.status(
                "Label rename canceled. Updated {} polygon(s) in {} file(s)."
                .format(changed_shapes, changed_files)
            )
            return False

        self.status(
            "Renamed '{}' to '{}' in {} polygon(s) across {} file(s)."
            .format(old_label, new_label, changed_shapes, changed_files)
        )
        return True

    def validateLabel(self, label):
        # no validation
        if self._config['validate_label'] is None:
            return True

        for i in range(self.uniqLabelList.count()):
            label_i = self.uniqLabelList.item(i).text()
            if self._config['validate_label'] in ['exact', 'instance']:
                if label_i == label:
                    return True
            if self._config['validate_label'] == 'instance':
                m = re.match(r'^{}-[0-9]*$'.format(label_i), label)
                if m:
                    return True
        return False

    def renameLabelItems(self, items, text, flags=None):
        if not items:
            return False

        text = text.strip()
        if not text:
            return False
        if not self.validateLabel(text):
            self.errorMessage(
                'Invalid label',
                "Invalid label '{}' with validation type '{}'"
                .format(text, self._config['validate_label'])
            )
            return False

        changed = False
        with QtCore.QSignalBlocker(self.labelList):
            for item in items:
                shape = self.labelList.get_shape_from_item(item)
                if shape is None:
                    continue
                if item.text() != text:
                    item.setText(text)
                item.setData(Qt.UserRole, text)
                if shape.label != text:
                    shape.label = text
                    changed = True
                if flags is not None and shape.flags != flags:
                    shape.flags = flags
                    changed = True

        if not changed:
            return True

        self.setDirty()
        self.canvas.update()
        self.labelDialog.addLabelHistory(text)
        if not self.uniqLabelList.findItems(text, Qt.MatchExactly):
            self.addUniqLabelItem(text)
        return True

    def editLabel(self, item=False):
        if item and not isinstance(item, QtWidgets.QListWidgetItem):
            raise TypeError('unsupported type of item: {}'.format(type(item)))

        if not self.canvas.editing():
            return
        if not item:
            item = self.currentItem()
        if item is None:
            return
        shape = self.labelList.get_shape_from_item(item)
        if shape is None:
            return
        old_label = shape.label
        text, flags = self.labelDialog.popUp(shape.label, flags=shape.flags)
        if text is None:
            return
        if text.strip() != old_label:
            old_flags = shape.flags
            shape.flags = flags
            self.setDirty()
            if not self.syncLabelRenameAcrossProject(old_label, text):
                shape.flags = old_flags
                return
        else:
            self.renameLabelItems([item], text, flags=flags)

    def editLabelInline(self, item):
        if item is None or not self.canvas.editing():
            return
        self.labelList.editItem(item)

    def editGlobalLabelInline(self, item):
        if item is None:
            return
        self.uniqLabelList.editItem(item)

    def uniqLabelItemChanged(self, item):
        old_label = item.data(Qt.UserRole)
        if old_label is None:
            old_label = item.text()
        new_label = item.text().strip()
        if new_label == old_label:
            return
        if not new_label:
            with QtCore.QSignalBlocker(self.uniqLabelList):
                item.setText(old_label)
            return
        if not self.syncLabelRenameAcrossProject(old_label, new_label):
            with QtCore.QSignalBlocker(self.uniqLabelList):
                item.setText(old_label)
            return
        with QtCore.QSignalBlocker(self.uniqLabelList):
            item.setText(new_label)
            item.setData(Qt.UserRole, new_label)
            self.dedupeUniqLabelList()

    def batchRenameLabels(self):
        if not self.canvas.editing():
            return
        items = self.labelList.selectedItems()
        if not items:
            return

        initial_text = items[0].text()
        text, ok = QtWidgets.QInputDialog.getText(
            self,
            'Batch Rename Labels',
            'New label for selected polygons:',
            text=initial_text,
        )
        if not ok:
            return
        self.renameLabelItems(items, text)

    def fileSearchChanged(self):
        self.importDirImages(
            self.lastOpenDir,
            pattern=self.fileSearch.text(),
            load=False,
        )

    def fileSelectionChanged(self):
        items = self.fileListWidget.selectedItems()
        if not items:
            return
        item = items[0]

        if not self.mayContinue():
            return

        selected_path = item.data(Qt.UserRole)
        if not selected_path:
            selected_path = str(item.text())
        currIndex = self.imageList.index(selected_path)
        if currIndex < len(self.imageList):
            filename = self.imageList[currIndex]
            if filename:
                self.loadFile(filename)

    # React to canvas signals.
    def shapeSelectionChanged(self, selected_shapes):
        self._noSelectionSlot = True
        for shape in self.canvas.selectedShapes:
            shape.selected = False
        self.labelList.clearSelection()
        self.canvas.selectedShapes = selected_shapes
        for shape in self.canvas.selectedShapes:
            shape.selected = True
            item = self.labelList.get_item_from_shape(shape)
            item.setSelected(True)
        self._noSelectionSlot = False
        n_selected = len(selected_shapes)
        self.actions.delete.setEnabled(n_selected)
        self.actions.copy.setEnabled(n_selected)
        self.actions.copyToClipboard.setEnabled(n_selected)
        self.actions.edit.setEnabled(n_selected == 1)
        self.actions.batchRename.setEnabled(n_selected >= 1)
        self.actions.shapeLineColor.setEnabled(n_selected)
        self.actions.shapeFillColor.setEnabled(n_selected)

    def addLabel(self, shape):
        item = QtWidgets.QListWidgetItem(shape.label)
        item.setFlags(
            item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEditable
        )
        item.setCheckState(Qt.Checked)
        item.setData(Qt.UserRole, shape.label)
        self.labelList.itemsToShapes.append((item, shape))
        self.labelList.addItem(item)
        if not self.uniqLabelList.findItems(shape.label, Qt.MatchExactly):
            self.addUniqLabelItem(shape.label)
        self.labelDialog.addLabelHistory(item.text())
        for action in self.actions.onShapesPresent:
            action.setEnabled(True)

    def remLabels(self, shapes):
        for shape in shapes:
            item = self.labelList.get_item_from_shape(shape)
            self.labelList.takeItem(self.labelList.row(item))

    def loadShapes(self, shapes, replace=True):
        self._noSelectionSlot = True
        for shape in shapes:
            self.addLabel(shape)
        self.labelList.clearSelection()
        self._noSelectionSlot = False
        self.canvas.loadShapes(shapes, replace=replace)

    def loadLabels(self, shapes):
        s = []
        for label, points, line_color, fill_color, shape_type, flags in shapes:
            shape = Shape(label=label, shape_type=shape_type)
            for x, y in points:
                shape.addPoint(QtCore.QPointF(x, y))
            shape.close()

            if line_color:
                shape.line_color = QtGui.QColor(*line_color)

            if fill_color:
                shape.fill_color = QtGui.QColor(*fill_color)

            default_flags = {}
            if self._config['label_flags']:
                for pattern, keys in self._config['label_flags'].items():
                    if re.match(pattern, label):
                        for key in keys:
                            default_flags[key] = False
            shape.flags = default_flags
            shape.flags.update(flags)

            s.append(shape)
        self.loadShapes(s)

    def loadFlags(self, flags):
        self.flag_widget.clear()
        for key, flag in flags.items():
            item = QtWidgets.QListWidgetItem(key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if flag else Qt.Unchecked)
            self.flag_widget.addItem(item)

    def saveLabels(self, filename):
        lf = LabelFile()

        def format_shape(s):
            return dict(
                label=s.label.encode('utf-8') if PY2 else s.label,
                line_color=s.line_color.getRgb()
                if s.line_color != self.lineColor else None,
                fill_color=s.fill_color.getRgb()
                if s.fill_color != self.fillColor else None,
                points=[(p.x(), p.y()) for p in s.points],
                shape_type=s.shape_type,
                flags=s.flags
            )

        shapes = [format_shape(shape) for shape in self.labelList.shapes]
        flags = {}
        for i in range(self.flag_widget.count()):
            item = self.flag_widget.item(i)
            key = item.text()
            flag = item.checkState() == Qt.Checked
            flags[key] = flag
        try:
            imagePath = osp.relpath(
                self.imagePath, osp.dirname(filename))
            imageData = self.imageData if self._config['store_data'] else None
            if osp.dirname(filename) and not osp.exists(osp.dirname(filename)):
                os.makedirs(osp.dirname(filename))
            lf.save(
                filename=filename,
                shapes=shapes,
                imagePath=imagePath,
                imageData=imageData,
                imageHeight=self.image.height(),
                imageWidth=self.image.width(),
                lineColor=self.lineColor.getRgb(),
                fillColor=self.fillColor.getRgb(),
                otherData=self.otherData,
                flags=flags,
            )
            self.labelFile = lf
            matched = []
            for i in range(self.fileListWidget.count()):
                item = self.fileListWidget.item(i)
                if item.data(Qt.UserRole) == self.imagePath:
                    matched.append(item)
            if len(matched) > 0:
                if len(matched) != 1:
                    raise RuntimeError('There are duplicate files.')
                matched[0].setCheckState(Qt.Checked)
            self.updateFileProgress()
            # disable allows next and previous image to proceed
            # self.filename = filename
            return True
        except LabelFileError as e:
            self.errorMessage('Error saving label data', '<b>%s</b>' % e)
            return False

    def copySelectedShape(self):
        added_shapes = self.canvas.copySelectedShapes()
        self.labelList.clearSelection()
        for shape in added_shapes:
            self.addLabel(shape)
        self.setDirty()

    def copyShapesToClipboard(self):
        if not self.canvas.selectedShapes:
            return
        self._copiedShapes = [shape.copy() for shape in self.canvas.selectedShapes]
        self._pasteCount = 0
        self.actions.pasteFromClipboard.setEnabled(True)
        self.status('Copied {} shape(s).'.format(len(self._copiedShapes)))

    def pasteShapesFromClipboard(self):
        if not self._copiedShapes or self.image.isNull():
            return
        self._pasteCount += 1
        step = 8 * self._pasteCount
        w = max(self.canvas.pixmap.width() - 1, 0)
        h = max(self.canvas.pixmap.height() - 1, 0)

        pasted_shapes = []
        for copied in self._copiedShapes:
            shape = copied.copy()
            moved_points = []
            for p in shape.points:
                x = min(max(p.x() + step, 0), w)
                y = min(max(p.y() + step, 0), h)
                moved_points.append(QtCore.QPointF(x, y))
            shape.points = moved_points
            shape.selected = False
            pasted_shapes.append(shape)

        self.canvas.loadShapes(pasted_shapes, replace=False)
        self.labelList.clearSelection()
        for shape in pasted_shapes:
            self.addLabel(shape)
        self.canvas.selectShapes(pasted_shapes)
        self.setDirty()

    def labelSelectionChanged(self):
        if self._noSelectionSlot:
            return
        if self.canvas.editing():
            selected_shapes = []
            for item in self.labelList.selectedItems():
                shape = self.labelList.get_shape_from_item(item)
                selected_shapes.append(shape)
            if selected_shapes:
                self.canvas.selectShapes(selected_shapes)

    def labelItemChanged(self, item):
        shape = self.labelList.get_shape_from_item(item)
        if shape is None:
            return
        label = str(item.text())
        previous_label = item.data(Qt.UserRole)
        if previous_label is None:
            previous_label = shape.label

        if label != shape.label:
            label = label.strip()
            if not label:
                with QtCore.QSignalBlocker(self.labelList):
                    item.setText(previous_label)
                return
            if not self.syncLabelRenameAcrossProject(previous_label, label):
                with QtCore.QSignalBlocker(self.labelList):
                    item.setText(previous_label)
                return
        else:  # User probably changed item visibility
            self.canvas.setShapeVisible(shape, item.checkState() == Qt.Checked)

    # Callback functions:

    def newShape(self):
        """Pop-up and give focus to the label editor.

        position MUST be in global coordinates.
        """
        items = self.uniqLabelList.selectedItems()
        text = None
        flags = {}
        if items:
            text = items[0].text()
        if self._config['display_label_popup'] or not text:
            # instance label auto increment
            if self._config['instance_label_auto_increment']:
                previous_label = self.labelDialog.edit.text()
                split = previous_label.split('-')
                if len(split) > 1 and split[-1].isdigit():
                    split[-1] = str(int(split[-1]) + 1)
                    instance_text = '-'.join(split)
                else:
                    instance_text = previous_label
                if instance_text != '':
                    text = instance_text
            text, flags = self.labelDialog.popUp(text)
            if text is None:
                self.labelDialog.edit.setText(previous_label)

        if text and not self.validateLabel(text):
            self.errorMessage('Invalid label',
                              "Invalid label '{}' with validation type '{}'"
                              .format(text, self._config['validate_label']))
            text = ''
        if text:
            self.labelList.clearSelection()
            self.addLabel(self.canvas.setLastLabel(text, flags))
            self.actions.editMode.setEnabled(True)
            self.actions.undoLastPoint.setEnabled(False)
            self.actions.undo.setEnabled(True)
            self.setDirty()
        else:
            self.canvas.undoLastLine()
            self.canvas.shapesBackups.pop()

    def scrollRequest(self, delta, orientation):
        units = - delta * 0.1  # natural scroll
        bar = self.scrollBars[orientation]
        bar.setValue(bar.value() + bar.singleStep() * units)

    def setZoom(self, value):
        self.actions.fitWidth.setChecked(False)
        self.actions.fitWindow.setChecked(False)
        self.zoomMode = self.MANUAL_ZOOM
        self.zoomWidget.setValue(value)

    def addZoom(self, increment=1.1):
        self.setZoom(self.zoomWidget.value() * increment)

    def zoomRequest(self, delta, pos):
        canvas_width_old = self.canvas.width()
        units = 1.1
        if delta < 0:
            units = 0.9
        self.addZoom(units)

        canvas_width_new = self.canvas.width()
        if canvas_width_old != canvas_width_new:
            canvas_scale_factor = canvas_width_new / canvas_width_old

            x_shift = round(pos.x() * canvas_scale_factor) - pos.x()
            y_shift = round(pos.y() * canvas_scale_factor) - pos.y()

            self.scrollBars[Qt.Horizontal].setValue(
                self.scrollBars[Qt.Horizontal].value() + x_shift)
            self.scrollBars[Qt.Vertical].setValue(
                self.scrollBars[Qt.Vertical].value() + y_shift)

    def setFitWindow(self, value=True):
        if value:
            self.actions.fitWidth.setChecked(False)
        self.zoomMode = self.FIT_WINDOW if value else self.MANUAL_ZOOM
        self.adjustScale()

    def setFitWidth(self, value=True):
        if value:
            self.actions.fitWindow.setChecked(False)
        self.zoomMode = self.FIT_WIDTH if value else self.MANUAL_ZOOM
        self.adjustScale()

    def togglePolygons(self, value):
        for item, shape in self.labelList.itemsToShapes:
            item.setCheckState(Qt.Checked if value else Qt.Unchecked)

    def loadFile(self, filename=None):
        """Load the specified file, or the last opened file if None."""
        # changing fileListWidget loads file
        if (filename in self.imageList and
                self.fileListWidget.currentRow() !=
                self.imageList.index(filename)):
            self.fileListWidget.setCurrentRow(self.imageList.index(filename))
            self.fileListWidget.repaint()
            return

        self.resetState()
        self.canvas.setEnabled(False)
        if filename is None:
            filename = self.settings.value('filename', '')
        filename = str(filename)
        if not QtCore.QFile.exists(filename):
            self.errorMessage(
                'Error opening file', 'No such file: <b>%s</b>' % filename)
            return False
        # assumes same name, but json extension
        self.status("Loading %s..." % osp.basename(str(filename)))
        label_file = osp.splitext(filename)[0] + '.json'
        if self.output_dir:
            label_file_without_path = osp.basename(label_file)
            label_file = osp.join(self.output_dir, label_file_without_path)
        if QtCore.QFile.exists(label_file) and \
                LabelFile.is_label_file(label_file):
            try:
                self.labelFile = LabelFile(label_file)
            except LabelFileError as e:
                self.errorMessage(
                    'Error opening file',
                    "<p><b>%s</b></p>"
                    "<p>Make sure <i>%s</i> is a valid label file."
                    % (e, label_file))
                self.status("Error reading %s" % label_file)
                return False
            self.imageData = self.labelFile.imageData
            self.imagePath = osp.join(
                osp.dirname(label_file),
                self.labelFile.imagePath,
            )
            if self.labelFile.lineColor is not None:
                self.lineColor = QtGui.QColor(*self.labelFile.lineColor)
            if self.labelFile.fillColor is not None:
                self.fillColor = QtGui.QColor(*self.labelFile.fillColor)
            self.otherData = self.labelFile.otherData
        else:
            self.imageData = LabelFile.load_image_file(filename)
            if self.imageData:
                self.imagePath = filename
            self.labelFile = None
        image = QtGui.QImage.fromData(self.imageData)

        if image.isNull():
            formats = ['*.{}'.format(fmt.data().decode())
                       for fmt in QtGui.QImageReader.supportedImageFormats()]
            self.errorMessage(
                'Error opening file',
                '<p>Make sure <i>{0}</i> is a valid image file.<br/>'
                'Supported image formats: {1}</p>'
                .format(filename, ','.join(formats)))
            self.status("Error reading %s" % filename)
            return False
        self.image = image
        self.filename = filename
        if self._config['keep_prev']:
            prev_shapes = self.canvas.shapes
        self.canvas.loadPixmap(QtGui.QPixmap.fromImage(image))
        if self._config['flags']:
            self.loadFlags({k: False for k in self._config['flags']})
        if self.labelFile:
            self.loadLabels(self.labelFile.shapes)
            if self.labelFile.flags is not None:
                self.loadFlags(self.labelFile.flags)
        if self._config['keep_prev'] and not self.labelList.shapes:
            self.loadShapes(prev_shapes, replace=False)
        self.setClean()
        self.canvas.setEnabled(True)
        # Always reset to fit-window when loading/switching images.
        self.zoomMode = self.FIT_WINDOW
        self.actions.fitWindow.setChecked(True)
        self.actions.fitWidth.setChecked(False)
        self.adjustScale(initial=True)
        self.paintCanvas()
        self.addRecentFile(self.filename)
        self.toggleActions(True)
        self.status("Loaded %s" % osp.basename(str(filename)))
        return True

    def resizeEvent(self, event):
        if self.canvas and not self.image.isNull()\
           and self.zoomMode != self.MANUAL_ZOOM:
            self.adjustScale()
        super(MainWindow, self).resizeEvent(event)

    def paintCanvas(self):
        assert not self.image.isNull(), "cannot paint null image"
        # 仅在手动缩放模式下启用留白，自动适应时关闭
        self.canvas.paddingEnabled = (self.zoomMode == self.MANUAL_ZOOM)
        self.canvas.scale = 0.01 * self.zoomWidget.value()
        self.canvas.adjustSize()
        self.canvas.update()

    def adjustScale(self, initial=False):
        value = self.scalers[self.FIT_WINDOW if initial else self.zoomMode]()
        self.zoomWidget.setValue(int(100 * value))

    def scaleFitWindow(self):
        """Figure out the size of the pixmap to fit the main widget."""
        e = 2.0  # So that no scrollbars are generated.
        w1 = self.centralWidget().width() - e
        h1 = self.centralWidget().height() - e
        a1 = w1 / h1
        # Calculate a new scale value based on the pixmap's aspect ratio.
        w2 = self.canvas.pixmap.width() - 0.0
        h2 = self.canvas.pixmap.height() - 0.0
        a2 = w2 / h2
        return w1 / w2 if a2 >= a1 else h1 / h2

    def scaleFitWidth(self):
        # The epsilon does not seem to work too well here.
        w = self.centralWidget().width() - 2.0
        return w / self.canvas.pixmap.width()

    def closeEvent(self, event):
        if not self.mayContinue():
            event.ignore()
        self.settings.setValue(
            'filename', self.filename if self.filename else '')
        self.settings.setValue('window/size', self.size())
        self.settings.setValue('window/position', self.pos())
        self.settings.setValue('window/state', self.saveState())
        self.settings.setValue('line/color', self.lineColor)
        self.settings.setValue('fill/color', self.fillColor)
        self.settings.setValue('reference_guide/color', self.referenceGuideColor)
        self.settings.setValue('recentFiles', self.recentFiles)
        # ask the use for where to save the labels
        # self.settings.setValue('window/geometry', self.saveGeometry())

    # User Dialogs #

    def loadRecent(self, filename):
        if self.mayContinue():
            self.loadFile(filename)

    def openPrevImg(self, _value=False):
        keep_prev = self._config['keep_prev']
        if QtGui.QGuiApplication.keyboardModifiers() == \
                (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier):
            self._config['keep_prev'] = True

        if not self.mayContinue():
            return

        if len(self.imageList) <= 0:
            return

        if self.filename is None:
            return

        currIndex = self.imageList.index(self.filename)
        if currIndex - 1 >= 0:
            filename = self.imageList[currIndex - 1]
            if filename:
                self.loadFile(filename)

        self._config['keep_prev'] = keep_prev

    def openNextImg(self, _value=False, load=True):
        keep_prev = self._config['keep_prev']
        if QtGui.QGuiApplication.keyboardModifiers() == \
                (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier):
            self._config['keep_prev'] = True

        if not self.mayContinue():
            return

        if len(self.imageList) <= 0:
            return

        filename = None
        if self.filename is None:
            filename = self.imageList[0]
        else:
            currIndex = self.imageList.index(self.filename)
            if currIndex + 1 < len(self.imageList):
                filename = self.imageList[currIndex + 1]
            else:
                filename = self.imageList[-1]
        self.filename = filename

        if self.filename and load:
            self.loadFile(self.filename)

        self._config['keep_prev'] = keep_prev

    def openFile(self, _value=False):
        if not self.mayContinue():
            return
        path = osp.dirname(str(self.filename)) if self.filename else '.'
        formats = ['*.{}'.format(fmt.data().decode())
                   for fmt in QtGui.QImageReader.supportedImageFormats()]
        filters = "Image & Label files (%s)" % ' '.join(
            formats + ['*%s' % LabelFile.suffix])
        filename = QtWidgets.QFileDialog.getOpenFileName(
            self, '%s - Choose Image or Label file' % __appname__,
            path, filters)
        if QT5:
            filename, _ = filename
        filename = str(filename)
        if filename:
            self.loadFile(filename)

    def changeOutputDirDialog(self, _value=False):
        default_output_dir = self.output_dir
        if default_output_dir is None and self.filename:
            default_output_dir = osp.dirname(self.filename)
        if default_output_dir is None:
            default_output_dir = self.currentPath()

        output_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self, '%s - Save/Load Annotations in Directory' % __appname__,
            default_output_dir,
            QtWidgets.QFileDialog.ShowDirsOnly |
            QtWidgets.QFileDialog.DontResolveSymlinks,
        )
        output_dir = str(output_dir)

        if not output_dir:
            return

        self.output_dir = output_dir

        self.statusBar().showMessage(
            '%s . Annotations will be saved/loaded in %s' %
            ('Change Annotations Dir', self.output_dir))
        self.statusBar().show()

        current_filename = self.filename
        self.importDirImages(self.lastOpenDir, load=False)

        if current_filename in self.imageList:
            # retain currently selected file
            self.fileListWidget.setCurrentRow(
                self.imageList.index(current_filename))
            self.fileListWidget.repaint()

    def saveFile(self, _value=False):
        assert not self.image.isNull(), "cannot save empty image"
        if self._config['flags'] or self.hasLabels():
            if self.labelFile:
                # DL20180323 - overwrite when in directory
                self._saveFile(self.labelFile.filename)
            elif self.output_file:
                self._saveFile(self.output_file)
                self.close()
            else:
                self._saveFile(self.saveFileDialog())

    def saveFileAs(self, _value=False):
        assert not self.image.isNull(), "cannot save empty image"
        if self.hasLabels():
            self._saveFile(self.saveFileDialog())

    def saveFileDialog(self):
        caption = '%s - Choose File' % __appname__
        filters = 'Label files (*%s)' % LabelFile.suffix
        if self.output_dir:
            dlg = QtWidgets.QFileDialog(
                self, caption, self.output_dir, filters
            )
        else:
            dlg = QtWidgets.QFileDialog(
                self, caption, self.currentPath(), filters
            )
        dlg.setDefaultSuffix(LabelFile.suffix[1:])
        dlg.setAcceptMode(QtWidgets.QFileDialog.AcceptSave)
        dlg.setOption(QtWidgets.QFileDialog.DontConfirmOverwrite, False)
        dlg.setOption(QtWidgets.QFileDialog.DontUseNativeDialog, False)
        basename = osp.basename(osp.splitext(self.filename)[0])
        if self.output_dir:
            default_labelfile_name = osp.join(
                self.output_dir, basename + LabelFile.suffix
            )
        else:
            default_labelfile_name = osp.join(
                self.currentPath(), basename + LabelFile.suffix
            )
        filename = dlg.getSaveFileName(
            self, 'Choose File', default_labelfile_name,
            'Label files (*%s)' % LabelFile.suffix)
        if QT5:
            filename, _ = filename
        filename = str(filename)
        return filename

    def _saveFile(self, filename):
        if filename and self.saveLabels(filename):
            self.addRecentFile(filename)
            self.setClean()

    def closeFile(self, _value=False):
        if not self.mayContinue():
            return
        self.resetState()
        self.setClean()
        self.toggleActions(False)
        self.canvas.setEnabled(False)
        self.actions.saveAs.setEnabled(False)

    def getLabelFile(self):
        if self.filename.lower().endswith('.json'):
            label_file = self.filename
        else:
            label_file = osp.splitext(self.filename)[0] + '.json'

        return label_file

    def deleteFile(self):
        mb = QtWidgets.QMessageBox
        msg = 'You are about to permanently delete this label file, ' \
              'proceed anyway?'
        answer = mb.warning(self, 'Attention', msg, mb.Yes | mb.No)
        if answer != mb.Yes:
            return

        label_file = self.getLabelFile()
        if osp.exists(label_file):
            os.remove(label_file)
            logger.info('Label file is removed: {}'.format(label_file))

            item = self.fileListWidget.currentItem()
            item.setCheckState(Qt.Unchecked)
            self.updateFileProgress()

            self.resetState()

    # Message Dialogs. #
    def hasLabels(self):
        if not self.labelList.itemsToShapes:
            self.errorMessage(
                'No objects labeled',
                'You must label at least one object to save the file.')
            return False
        return True

    def hasLabelFile(self):
        if self.filename is None:
            return False

        label_file = self.getLabelFile()
        return osp.exists(label_file)

    def mayContinue(self):
        if not self.dirty:
            return True
        mb = QtWidgets.QMessageBox
        msg = 'Save annotations to "{}" before closing?'.format(self.filename)
        answer = mb.question(self,
                             'Save annotations?',
                             msg,
                             mb.Save | mb.Discard | mb.Cancel,
                             mb.Save)
        if answer == mb.Discard:
            return True
        elif answer == mb.Save:
            self.saveFile()
            return True
        else:  # answer == mb.Cancel
            return False

    def errorMessage(self, title, message):
        return QtWidgets.QMessageBox.critical(
            self, title, '<p><b>%s</b></p>%s' % (title, message))

    def currentPath(self):
        return osp.dirname(str(self.filename)) if self.filename else '.'

    def chooseColor1(self):
        color = self.colorDialog.getColor(
            self.lineColor, 'Choose line color', default=DEFAULT_LINE_COLOR)
        if color:
            self.lineColor = color
            # Change the color for all shape lines:
            Shape.line_color = self.lineColor
            self.canvas.update()
            self.setDirty()

    def chooseColor2(self):
        color = self.colorDialog.getColor(
            self.fillColor, 'Choose fill color', default=DEFAULT_FILL_COLOR)
        if color:
            self.fillColor = color
            Shape.fill_color = self.fillColor
            self.canvas.update()
            self.setDirty()

    def toggleKeepPrevMode(self):
        self._config['keep_prev'] = not self._config['keep_prev']

    def deleteSelectedPoint(self):
        if self.canvas.deleteSelectedPoint():
            self.setDirty()
            return True
        return False

    def deleteSelectedShapeOrPoint(self):
        if self.deleteSelectedPoint():
            return
        self.deleteSelectedShape()

    def deleteSelectedShape(self):
        yes, no = QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.No
        msg = 'You are about to permanently delete {} polygons, ' \
              'proceed anyway?'.format(len(self.canvas.selectedShapes))
        if yes == QtWidgets.QMessageBox.warning(self, 'Attention', msg,
                                                yes | no):
            self.remLabels(self.canvas.deleteSelected())
            self.setDirty()
            if self.noShapes():
                for action in self.actions.onShapesPresent:
                    action.setEnabled(False)

    def chshapeLineColor(self):
        color = self.colorDialog.getColor(
            self.lineColor, 'Choose line color', default=DEFAULT_LINE_COLOR)
        if color:
            for shape in self.canvas.selectedShapes:
                shape.line_color = color
            self.canvas.update()
            self.setDirty()

    def chshapeFillColor(self):
        color = self.colorDialog.getColor(
            self.fillColor, 'Choose fill color', default=DEFAULT_FILL_COLOR)
        if color:
            for shape in self.canvas.selectedShapes:
                shape.fill_color = color
            self.canvas.update()
            self.setDirty()

    def chooseReferenceGuideColor(self):
        color = self.colorDialog.getColor(
            self.referenceGuideColor,
            'Choose reference guide color',
            default=QtGui.QColor(255, 80, 120, 150),
        )
        if color:
            self.referenceGuideColor = color
            self.canvas.setReferenceGuideColor(color)

    def copyShape(self):
        self.canvas.endMove(copy=True)
        self.labelList.clearSelection()
        for shape in self.canvas.selectedShapes:
            self.addLabel(shape)
        self.setDirty()

    def moveShape(self):
        self.canvas.endMove(copy=False)
        self.setDirty()

    def openDirDialog(self, _value=False, dirpath=None):
        if not self.mayContinue():
            return

        defaultOpenDirPath = dirpath if dirpath else '.'
        if self.lastOpenDir and osp.exists(self.lastOpenDir):
            defaultOpenDirPath = self.lastOpenDir
        else:
            defaultOpenDirPath = osp.dirname(self.filename) \
                if self.filename else '.'

        targetDirPath = str(QtWidgets.QFileDialog.getExistingDirectory(
            self, '%s - Open Directory' % __appname__, defaultOpenDirPath,
            QtWidgets.QFileDialog.ShowDirsOnly |
            QtWidgets.QFileDialog.DontResolveSymlinks))
        self.importDirImages(targetDirPath)

    @property
    def imageList(self):
        lst = []
        for i in range(self.fileListWidget.count()):
            item = self.fileListWidget.item(i)
            full_path = item.data(Qt.UserRole)
            lst.append(full_path if full_path else item.text())
        return lst

    def importDirImages(self, dirpath, pattern=None, load=True):
        self.actions.openNextImg.setEnabled(True)
        self.actions.openPrevImg.setEnabled(True)

        if not self.mayContinue() or not dirpath:
            return

        self.lastOpenDir = dirpath
        self.filename = None
        self.fileListWidget.clear()
        for filename in self.scanAllImages(dirpath):
            basename = osp.basename(filename)
            if pattern and pattern not in basename and pattern not in filename:
                continue
            label_file = osp.splitext(filename)[0] + '.json'
            if self.output_dir:
                label_file_without_path = osp.basename(label_file)
                label_file = osp.join(self.output_dir, label_file_without_path)
            item = QtWidgets.QListWidgetItem(basename)
            item.setData(Qt.UserRole, filename)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if QtCore.QFile.exists(label_file) and \
                    LabelFile.is_label_file(label_file):
                item.setCheckState(Qt.Checked)
            else:
                item.setCheckState(Qt.Unchecked)
            self.fileListWidget.addItem(item)
        self.updateFileProgress()
        self.refreshLabelStatsPanel()
        self.openNextImg(load=load)

    def scanAllImages(self, folderPath):
        extensions = ['.%s' % fmt.data().decode("ascii").lower()
                      for fmt in QtGui.QImageReader.supportedImageFormats()]
        images = []

        for root, dirs, files in os.walk(folderPath):
            for file in files:
                if file.lower().endswith(tuple(extensions)):
                    relativePath = osp.join(root, file)
                    images.append(relativePath)
        images.sort(key=lambda x: x.lower())
        return images

    def updateFileProgress(self):
        total = self.fileListWidget.count()
        done = 0
        for i in range(total):
            item = self.fileListWidget.item(i)
            if item.checkState() == Qt.Checked:
                done += 1
        remaining = max(total - done, 0)
        self.fileProgressLabel.setText('已完成: {} | 剩余: {}'.format(done, remaining))
