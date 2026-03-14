from qtpy import QtCore
from qtpy import QtGui
from qtpy import QtWidgets

from labelme import QT5
from labelme.shape import Shape
import labelme.utils


# TODO(unknown):
# - [maybe] Find optimal epsilon value.


CURSOR_DEFAULT = QtCore.Qt.ArrowCursor
CURSOR_POINT = QtCore.Qt.PointingHandCursor
CURSOR_DRAW = QtCore.Qt.CrossCursor
CURSOR_MOVE = QtCore.Qt.ClosedHandCursor
CURSOR_GRAB = QtCore.Qt.OpenHandCursor

EXTRA_SCROLL_PADDING = 400  # 额外留白像素，方便高倍缩放时继续拖动/滚动


class Canvas(QtWidgets.QWidget):

    zoomRequest = QtCore.Signal(int, QtCore.QPoint)
    scrollRequest = QtCore.Signal(int, int)
    newShape = QtCore.Signal()
    selectionChanged = QtCore.Signal(list)
    shapeMoved = QtCore.Signal()
    drawingPolygon = QtCore.Signal(bool)
    edgeSelected = QtCore.Signal(bool)
    vertexSelected = QtCore.Signal(bool)

    CREATE, EDIT = 0, 1

    # polygon, rectangle, line, or point
    _createMode = 'polygon'

    _fill_drawing = False

    def __init__(self, *args, **kwargs):
        self.epsilon = kwargs.pop('epsilon', 10.0)
        super(Canvas, self).__init__(*args, **kwargs)
        # Initialise local state.
        self.shape = None
        self.selectedEdge = None
        self.mode = self.EDIT
        self.shapes = []
        self.shapesBackups = []
        self.current = None
        self.selectedShapes = []  # save the selected shapes here
        self.selectedShapesCopy = []
        self.lineColor = QtGui.QColor(0, 0, 255)
        # self.line represents:
        #   - createMode == 'polygon': edge from last point to current
        #   - createMode == 'rectangle': diagonal line of the rectangle
        #   - createMode == 'line': the line
        #   - createMode == 'point': the point
        self.line = Shape(line_color=self.lineColor)
        self.prevPoint = QtCore.QPoint()
        self.prevMovePoint = QtCore.QPoint()
        self.offsets = QtCore.QPoint(), QtCore.QPoint()
        self.scale = 1.0
        self.pixmap = QtGui.QPixmap()
        self.visible = {}
        self._hideBackround = False
        self.hideBackround = False
        self.hShape = None
        self.hVertex = None
        self.hEdge = None
        self.movingShape = False
        self.shapeMoveArmed = False
        self.shapeMoveTarget = None
        self.shapeMoveLocked = True
        self.leftPressActive = False
        self.longPressShape = None
        self.longPressPos = None
        self.longPressTimer = QtCore.QTimer(self)
        self.longPressTimer.setSingleShot(True)
        self.longPressTimer.setInterval(500)
        self.longPressTimer.timeout.connect(self.activateLongPressMove)
        self.paddingEnabled = False
        self.referenceGuidesEnabled = False
        self.referencePoint = None
        self.referenceGuideColor = QtGui.QColor(255, 80, 120, 150)
        self._painter = QtGui.QPainter()
        self._cursor = CURSOR_DEFAULT
        # Menus:
        # 0: right-click without selection and dragging of shapes
        # 1: right-click with selection and dragging of shapes
        self.menus = (QtWidgets.QMenu(), QtWidgets.QMenu())
        # Set widget options.
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.WheelFocus)

    def fillDrawing(self):
        return self._fill_drawing

    def setFillDrawing(self, value):
        self._fill_drawing = value

    def toggleReferenceGuides(self, enabled):
        self.referenceGuidesEnabled = bool(enabled)
        if not self.referenceGuidesEnabled:
            self.referencePoint = None
        self.update()

    def setReferenceGuideColor(self, color):
        qcolor = QtGui.QColor(color)
        if not qcolor.isValid():
            return
        self.referenceGuideColor = qcolor
        self.update()

    @property
    def createMode(self):
        return self._createMode

    @createMode.setter
    def createMode(self, value):
        if value not in ['polygon', 'rectangle', 'circle',
           'line', 'point', 'linestrip']:
            raise ValueError('Unsupported createMode: %s' % value)
        self._createMode = value

    def storeShapes(self):
        shapesBackup = []
        for shape in self.shapes:
            shapesBackup.append(shape.copy())
        if len(self.shapesBackups) >= 10:
            self.shapesBackups = self.shapesBackups[-9:]
        self.shapesBackups.append(shapesBackup)

    @property
    def isShapeRestorable(self):
        if len(self.shapesBackups) < 2:
            return False
        return True

    def restoreShape(self):
        if not self.isShapeRestorable:
            return
        self.shapesBackups.pop()  # latest
        shapesBackup = self.shapesBackups.pop()
        self.shapes = shapesBackup
        self.selectedShapes = []
        for shape in self.shapes:
            shape.selected = False
        self.repaint()

    def enterEvent(self, ev):
        self.overrideCursor(self._cursor)

    def leaveEvent(self, ev):
        self.restoreCursor()

    def focusOutEvent(self, ev):
        self.restoreCursor()

    def isVisible(self, shape):
        return self.visible.get(shape, True)

    def drawing(self):
        return self.mode == self.CREATE

    def editing(self):
        return self.mode == self.EDIT

    def setEditing(self, value=True):
        self.mode = self.EDIT if value else self.CREATE
        if not value:  # Create
            self.unHighlight()
            self.deSelectShape()

    def unHighlight(self):
        if self.hShape:
            self.hShape.highlightClear()
        self.hVertex = self.hShape = None
        self.hEdge = None
        self.selectedEdge = None

    def armShapeMove(self, shape):
        self.shapeMoveArmed = shape is not None
        self.shapeMoveTarget = shape
        if shape is not None:
            self.setToolTip(
                "Shape '%s' unlocked for move. Drag once to move." % shape.label
            )
            self.setStatusTip(self.toolTip())

    def disarmShapeMove(self):
        self.shapeMoveArmed = False
        self.shapeMoveTarget = None

    def setShapeMoveLocked(self, value):
        self.shapeMoveLocked = bool(value)
        if not self.shapeMoveLocked:
            self.disarmShapeMove()
        self.update()

    def startLongPressMove(self, shape, pos):
        self.leftPressActive = True
        self.longPressShape = shape
        self.longPressPos = pos
        if shape is None or not self.shapeMoveLocked:
            self.longPressTimer.stop()
            return
        self.longPressTimer.start()

    def stopLongPressMove(self):
        self.leftPressActive = False
        self.longPressShape = None
        self.longPressPos = None
        self.longPressTimer.stop()

    def activateLongPressMove(self):
        if not self.leftPressActive or self.longPressShape is None:
            return
        self.armShapeMove(self.longPressShape)
        if self.longPressPos is not None:
            self.calculateOffsets(self.longPressShape, self.longPressPos)
            self.prevPoint = self.longPressPos
        self.update()

    def selectedVertex(self):
        return self.hVertex is not None

    def _isClickStartPoint(self, current_pos, start_pos, threshold=5):
        if current_pos is None or start_pos is None:
            return False
        dx = current_pos.x() - start_pos.x()
        dy = current_pos.y() - start_pos.y()
        distance = (dx**2 + dy**2)**0.5
        return distance < threshold

    def mouseMoveEvent(self, ev):
        """Update line with last point and current coordinates."""
        try:
            if QT5:
                pos = self.transformPos(ev.localPos())
            else:
                pos = self.transformPos(ev.posF())
        except AttributeError:
            return

        # 记录鼠标位置（限制在图像范围内）
        raw_pos = pos
        clamped_pos = self.clampToPixmap(raw_pos)
        self.prevMovePoint = clamped_pos
        self.restoreCursor()

        if self.referenceGuidesEnabled:
            self.referencePoint = clamped_pos
            self.update()

        pos = raw_pos

        # Polygon drawing.
        if self.drawing():
            self.line.shape_type = self.createMode

            self.overrideCursor(CURSOR_DRAW)
            if not self.current:
                return

            color = self.lineColor
            if self.outOfPixmap(pos):
                # Don't allow the user to draw outside the pixmap.
                # Project the point to the pixmap's edges.
                pos = self.intersectionPoint(self.current[-1], pos)
            elif len(self.current) > 1 and self.createMode == 'polygon' and\
                    self.closeEnough(pos, self.current[0]):
                # Attract line to starting point and
                # colorise to alert the user.
                pos = self.current[0]
                color = self.current.line_color
                self.overrideCursor(CURSOR_POINT)
                self.current.highlightVertex(0, Shape.NEAR_VERTEX)
            if self.createMode in ['polygon', 'linestrip']:
                self.line[0] = self.current[-1]
                self.line[1] = pos
            elif self.createMode == 'rectangle':
                self.line.points = [self.current[0], pos]
                self.line.close()
            elif self.createMode == 'circle':
                self.line.points = [self.current[0], pos]
                self.line.shape_type = "circle"
            elif self.createMode == 'line':
                self.line.points = [self.current[0], pos]
                self.line.close()
            elif self.createMode == 'point':
                self.line.points = [self.current[0]]
                self.line.close()
            self.line.line_color = color
            self.repaint()
            self.current.highlightClear()
            return

        # Polygon copy moving.
        if QtCore.Qt.RightButton & ev.buttons():
            if self.selectedShapesCopy and self.prevPoint:
                self.overrideCursor(CURSOR_MOVE)
                self.boundedMoveShapes(self.selectedShapesCopy, pos)
                self.repaint()
            elif self.selectedShapes:
                self.selectedShapesCopy = \
                    [s.copy() for s in self.selectedShapes]
                self.repaint()
            return

        # Polygon/Vertex moving.
        self.movingShape = False
        if QtCore.Qt.LeftButton & ev.buttons():
            if (self.longPressTimer.isActive() and self.longPressPos is not None and
                    labelme.utils.distance(pos - self.longPressPos) > 3):
                self.longPressTimer.stop()
            if self.selectedVertex():
                self.boundedMoveVertex(pos)
                self.repaint()
                self.movingShape = True
            elif (
                self.selectedShapes and self.prevPoint and (
                    not self.shapeMoveLocked or
                    (self.shapeMoveArmed and self.shapeMoveTarget in self.selectedShapes)
                )
            ):
                self.overrideCursor(CURSOR_MOVE)
                self.boundedMoveShapes(self.selectedShapes, pos)
                self.repaint()
                self.movingShape = True
            return

        # Just hovering over the canvas, 2 posibilities:
        # - Highlight shapes
        # - Highlight vertex
        # Update shape/vertex fill and tooltip value accordingly.
        self.setToolTip("Image")
        for shape in reversed([s for s in self.shapes if self.isVisible(s)]):
            # Look for a nearby vertex to highlight. If that fails,
            # check if we happen to be inside a shape.
            index = shape.nearestVertex(pos, self.epsilon / self.scale)
            index_edge = shape.nearestEdge(pos, self.epsilon / self.scale)
            if index is not None:
                if self.selectedVertex():
                    self.hShape.highlightClear()
                self.hVertex = index
                self.hShape = shape
                self.hEdge = index_edge
                self.selectedEdge = (
                    self.hShape, self.hEdge) if self.hEdge is not None else None
                shape.highlightVertex(index, shape.MOVE_VERTEX)
                self.overrideCursor(CURSOR_POINT)
                self.setToolTip("Click & drag to move point")
                self.setStatusTip(self.toolTip())
                self.update()
                break
            elif shape.containsPoint(pos):
                if self.selectedVertex():
                    self.hShape.highlightClear()
                self.hVertex = None
                self.hShape = shape
                self.hEdge = index_edge
                self.selectedEdge = (
                    self.hShape, self.hEdge) if self.hEdge is not None else None
                self.setToolTip(
                    "Double-click or long-press to move shape '%s'" % shape.label)
                self.setStatusTip(self.toolTip())
                self.overrideCursor(CURSOR_GRAB)
                self.update()
                break
        else:  # Nothing found, clear highlights, reset state.
            if self.hShape:
                self.hShape.highlightClear()
                self.update()
            self.hVertex, self.hShape, self.hEdge = None, None, None
            self.selectedEdge = None
        self.edgeSelected.emit(self.hEdge is not None)
        self.vertexSelected.emit(self.hVertex is not None)

    def addPointToEdge(self, point=None):
        """Add a vertex on the selected edge and keep it inside image bounds."""
        if self.selectedEdge is None:
            return

        # QAction may pass a bool payload; fallback to last cursor position.
        if point is None or isinstance(point, bool):
            point = self.prevMovePoint if self.prevMovePoint is not None else None
        if point is None:
            return

        if not hasattr(point, "x") or not hasattr(point, "y"):
            return

        point = self.clampToPixmap(point)

        shape, index = self.selectedEdge
        if shape is not None and hasattr(shape, "insertPoint") and index is not None:
            shape.insertPoint(index, point)
            self.hShape = shape
            self.hVertex = index
            self.hEdge = None
            self.selectedEdge = None
            shape.highlightVertex(index, shape.MOVE_VERTEX)
            self.update()

    def mousePressEvent(self, ev):
        if QT5:
            pos = self.transformPos(ev.localPos())
        else:
            pos = self.transformPos(ev.posF())
        click_pos = self.clampToPixmap(pos)

        if self.referenceGuidesEnabled and ev.button() == QtCore.Qt.LeftButton:
            self.referencePoint = click_pos

        if ev.button() == QtCore.Qt.LeftButton:
            if self.drawing():
                if self.current:
                    # Add point to existing shape.
                    if self.createMode == 'polygon':
                        # Close polygon when clicking near the first point.
                        if len(self.current.points) > 1 and self._isClickStartPoint(click_pos, self.current.points[0]):
                            self.current.addPoint(self.current.points[0])  # 闭合到初始点
                            self.current.setClosed(True)
                            self.finalise()  # 完成绘制
                        else:
                            self.current.addPoint(self.line[1])
                            self.line[0] = self.current[-1]
                            if self.current.isClosed():
                                self.finalise()
                    elif self.createMode in ['rectangle', 'circle', 'line']:
                        assert len(self.current.points) == 1
                        self.current.points = self.line.points
                        self.finalise()
                    elif self.createMode == 'linestrip':
                        self.current.addPoint(self.line[1])
                        self.line[0] = self.current[-1]
                        if int(ev.modifiers()) == QtCore.Qt.ControlModifier:
                            self.finalise()
                else:
                    # Create new shape.
                    self.current = Shape(shape_type=self.createMode)
                    self.current.addPoint(click_pos)
                    if self.createMode == 'point':
                        self.finalise()
                    else:
                        if self.createMode == 'circle':
                            self.current.shape_type = 'circle'
                        self.line.points = [click_pos, click_pos]
                        self.setHiding()
                        self.drawingPolygon.emit(True)
                        self.update()
            else:
                group_mode = (int(ev.modifiers()) == QtCore.Qt.ControlModifier)
                pressed_shape = None
                for shape in reversed(self.shapes):
                    if self.isVisible(shape) and shape.containsPoint(pos):
                        pressed_shape = shape
                        break
                self.selectShapePoint(pos, multiple_selection_mode=group_mode)
                self.prevPoint = pos
                if not (self.shapeMoveArmed and self.shapeMoveTarget in self.selectedShapes):
                    self.disarmShapeMove()
                self.startLongPressMove(pressed_shape, pos)
                self.repaint()
        elif ev.button() == QtCore.Qt.RightButton and self.editing():
            group_mode = (int(ev.modifiers()) == QtCore.Qt.ControlModifier)
            self.selectShapePoint(pos, multiple_selection_mode=group_mode)
            self.prevPoint = pos
            self.repaint()

    def mouseReleaseEvent(self, ev):
        if ev.button() == QtCore.Qt.RightButton:
            menu = self.menus[len(self.selectedShapesCopy) > 0]
            self.restoreCursor()
            if not menu.exec_(self.mapToGlobal(ev.pos())) \
                    and self.selectedShapesCopy:
                # Cancel the move by deleting the shadow copy.
                self.selectedShapesCopy = []
                self.repaint()
        elif ev.button() == QtCore.Qt.LeftButton and self.selectedShapes:
            self.overrideCursor(CURSOR_GRAB)
        if self.movingShape:
            self.storeShapes()
            self.shapeMoved.emit()
        if ev.button() == QtCore.Qt.LeftButton:
            self.stopLongPressMove()
            self.disarmShapeMove()

    def endMove(self, copy):
        assert self.selectedShapes and self.selectedShapesCopy
        assert len(self.selectedShapesCopy) == len(self.selectedShapes)
        # del shape.fill_color
        # del shape.line_color
        if copy:
            for i, shape in enumerate(self.selectedShapesCopy):
                self.shapes.append(shape)
                self.selectedShapes[i].selected = False
                self.selectedShapes[i] = shape
        else:
            for i, shape in enumerate(self.selectedShapesCopy):
                self.selectedShapes[i].points = shape.points
        self.selectedShapesCopy = []
        self.repaint()
        self.storeShapes()
        return True

    def hideBackroundShapes(self, value):
        self.hideBackround = value
        if self.selectedShapes:
            # Only hide other shapes if there is a current selection.
            # Otherwise the user will not be able to select a shape.
            self.setHiding(True)
            self.repaint()

    def setHiding(self, enable=True):
        self._hideBackround = self.hideBackround if enable else False

    def canCloseShape(self):
        return self.drawing() and self.current and len(self.current) > 2

    def mouseDoubleClickEvent(self, ev):
        # We need at least 4 points here, since the mousePress handler
        # adds an extra one before this handler is called.
        if self.canCloseShape() and len(self.current) > 3:
            self.current.popPoint()
            self.finalise()
            return

        if self.editing():
            if QT5:
                pos = self.transformPos(ev.localPos())
            else:
                pos = self.transformPos(ev.posF())
            for shape in reversed(self.shapes):
                if self.isVisible(shape) and shape.containsPoint(pos):
                    self.selectShapes([shape])
                    self.calculateOffsets(shape, pos)
                    self.prevPoint = pos
                    self.armShapeMove(shape)
                    self.stopLongPressMove()
                    self.update()
                    return

    def selectShapes(self, shapes):
        self.setHiding()
        self.selectionChanged.emit(shapes)
        self.update()

    def selectShapePoint(self, point, multiple_selection_mode):
        """Select the first shape created which contains this point."""
        if self.selectedVertex():  # A vertex is marked for selection.
            index, shape = self.hVertex, self.hShape
            shape.highlightVertex(index, shape.MOVE_VERTEX)
        else:
            for shape in reversed(self.shapes):
                if self.isVisible(shape) and shape.containsPoint(point):
                    self.calculateOffsets(shape, point)
                    self.setHiding()
                    if multiple_selection_mode:
                        if shape not in self.selectedShapes:
                            self.selectionChanged.emit(
                                self.selectedShapes + [shape])
                    else:
                        self.selectionChanged.emit([shape])
                    return
        self.deSelectShape()

    def calculateOffsets(self, shape, point):
        rect = shape.boundingRect()
        x1 = rect.x() - point.x()
        y1 = rect.y() - point.y()
        x2 = (rect.x() + rect.width() - 1) - point.x()
        y2 = (rect.y() + rect.height() - 1) - point.y()
        self.offsets = QtCore.QPoint(x1, y1), QtCore.QPoint(x2, y2)

    def boundedMoveVertex(self, pos):
        index, shape = self.hVertex, self.hShape
        point = shape[index]
        pos = self.clampToPixmap(pos)
        if self.outOfPixmap(pos):
            pos = self.intersectionPoint(point, pos)
        shape.moveVertexBy(index, pos - point)

    def boundedMoveShapes(self, shapes, pos):
        if self.outOfPixmap(pos):
            return False  # No need to move
        o1 = pos + self.offsets[0]
        if self.outOfPixmap(o1):
            pos -= QtCore.QPoint(min(0, o1.x()), min(0, o1.y()))
        o2 = pos + self.offsets[1]
        if self.outOfPixmap(o2):
            pos += QtCore.QPoint(min(0, self.pixmap.width() - o2.x()),
                                 min(0, self.pixmap.height() - o2.y()))
        # XXX: The next line tracks the new position of the cursor
        # relative to the shape, but also results in making it
        # a bit "shaky" when nearing the border and allows it to
        # go outside of the shape's area for some reason.
        # self.calculateOffsets(self.selectedShapes, pos)
        dp = pos - self.prevPoint
        if dp:
            for shape in shapes:
                shape.moveBy(dp)
            self.prevPoint = pos
            return True
        return False

    def deSelectShape(self):
        if self.selectedShapes:
            self.setHiding(False)
            self.disarmShapeMove()
            self.stopLongPressMove()
            self.selectionChanged.emit([])
            self.update()

    def deleteSelectedPoint(self):
        if not self.selectedVertex() or self.hShape is None:
            return False

        shape = self.hShape
        index = self.hVertex
        if index is None or index < 0 or index >= len(shape.points):
            return False

        min_points = {
            'polygon': 3,
            'linestrip': 2,
            'line': 2,
            'rectangle': 2,
            'circle': 2,
            'point': 1,
        }.get(shape.shape_type, 1)
        if len(shape.points) <= min_points:
            return False

        del shape.points[index]
        shape.highlightClear()
        self.hVertex = None
        self.hEdge = None
        self.selectedEdge = None
        self.storeShapes()
        self.update()
        return True

    def deleteSelected(self):
        selected = []
        for shape in self.shapes:
            if hasattr(shape, 'selected') and shape.selected:
                selected.append(shape)
        for shape in selected:
            if shape in self.shapes:
                self.shapes.remove(shape)
        self.update()
        return selected

    def copySelectedShapes(self):
        if self.selectedShapes:
            self.selectedShapesCopy = [s.copy() for s in self.selectedShapes]
            self.boundedShiftShapes(self.selectedShapesCopy)
            self.endMove(copy=True)
        return self.selectedShapes

    def boundedShiftShapes(self, shapes):
        # Try to move in one direction, and if it fails in another.
        # Give up if both fail.
        point = shapes[0][0]
        offset = QtCore.QPoint(2.0, 2.0)
        self.offsets = QtCore.QPoint(), QtCore.QPoint()
        self.prevPoint = point
        if not self.boundedMoveShapes(shapes, point - offset):
            self.boundedMoveShapes(shapes, point + offset)

    def paintEvent(self, event):
        if not self.pixmap:
            return super(Canvas, self).paintEvent(event)

        p = self._painter
        p.begin(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.HighQualityAntialiasing)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)

        p.scale(self.scale, self.scale)
        p.translate(self.offsetToCenter())

        p.drawPixmap(0, 0, self.pixmap)
        Shape.scale = self.scale
        for shape in self.shapes:
            if (shape.selected or not self._hideBackround) and \
                    self.isVisible(shape):
                shape.hovered = shape == self.hShape
                shape.hovered_edge_index = (
                    self.hEdge if shape == self.hShape else None
                )
                shape.fill = shape.selected
                shape.paint(p)
        if self.current:
            self.current.paint(p)
            self.line.paint(p)
        if self.selectedShapesCopy:
            for s in self.selectedShapesCopy:
                s.paint(p)

        if (self.fillDrawing() and self.createMode == 'polygon' and
                self.current is not None and len(self.current.points) >= 2):
            drawing_shape = self.current.copy()
            drawing_shape.addPoint(self.line[1])
            drawing_shape.fill = True
            drawing_shape.fill_color.setAlpha(64)
            drawing_shape.paint(p)

        if self.referenceGuidesEnabled and self.referencePoint is not None:
            guide_pen = QtGui.QPen(self.referenceGuideColor)
            guide_pen.setWidth(max(2, int(round(1.5 / self.scale))))
            guide_pen.setStyle(QtCore.Qt.DotLine)
            p.setPen(guide_pen)
            x = self.referencePoint.x()
            y = self.referencePoint.y()
            w = self.pixmap.width() - 1
            h = self.pixmap.height() - 1
            p.drawLine(QtCore.QPointF(0, y), QtCore.QPointF(w, y))
            p.drawLine(QtCore.QPointF(x, 0), QtCore.QPointF(x, h))

        p.end()

    def transformPos(self, point):
        """Convert from widget-logical coordinates to painter-logical ones."""
        return point / self.scale - self.offsetToCenter()

    def offsetToCenter(self):
        s = self.scale
        area = super(Canvas, self).size()
        w, h = self.pixmap.width() * s, self.pixmap.height() * s
        aw, ah = area.width(), area.height()
        x = (aw - w) / (2 * s) if aw > w else 0
        y = (ah - h) / (2 * s) if ah > h else 0
        return QtCore.QPoint(x, y)

    def outOfPixmap(self, p):
        w, h = self.pixmap.width(), self.pixmap.height()
        return not (0 <= p.x() < w and 0 <= p.y() < h)

    def clampToPixmap(self, p):
        """Limit a QPoint to the current pixmap bounds."""
        w, h = self.pixmap.width(), self.pixmap.height()
        x = min(max(0, int(p.x())), max(w - 1, 0))
        y = min(max(0, int(p.y())), max(h - 1, 0))
        return QtCore.QPoint(x, y)

    def finalise(self):
        assert self.current
        self.current.close()
        self.shapes.append(self.current)
        self.storeShapes()
        self.current = None
        self.setHiding(False)
        self.newShape.emit()
        self.update()

    def closeEnough(self, p1, p2):
        # d = distance(p1 - p2)
        # m = (p1-p2).manhattanLength()
        # print "d %.2f, m %d, %.2f" % (d, m, d - m)
        # divide by scale to allow more precision when zoomed in
        if p1 is None or p2 is None:
            return False
        try:
            d = labelme.utils.distance(p1 - p2)
        except Exception:
            return False
        return d < self.epsilon / self.scale
    def intersectionPoint(self, p1, p2):
        # Intersect segment p1->p2 with image bounds.
        if self.pixmap is None or self.pixmap.isNull():
            return p2
        if not self.outOfPixmap(p2):
            return p2

        p2_clamped = self.clampToPixmap(p2)
        w = max(self.pixmap.width() - 1, 0)
        h = max(self.pixmap.height() - 1, 0)
        rect_points = [(0, 0), (w, 0), (w, h), (0, h)]
        edges = list(self.intersectingEdges(
            (p1.x(), p1.y()),
            (p2.x(), p2.y()),
            rect_points,
        ))
        if not edges:
            return p2_clamped

        d, i, (x, y) = min(edges)
        return QtCore.QPoint(int(round(x)), int(round(y)))

    def intersectingEdges(self, a1, a2, points):
        """
        a1, a2: Two endpoints of line segment 1, (x,y) tuples
        points: List of all shape points, elements are (x,y) tuples (not QPointF)
        """
        # Return early only when there are fewer than 2 points.
        if len(points) < 2:
            return []
        # 原有逻辑保留
        for i in range(len(points)):
            j = (i + 1) % len(points)
            if i >= len(points) or j >= len(points):
                continue
            x3, y3 = points[i]
            x4, y4 = points[j]
            d = (a1[0] - a2[0]) * (y3 - y4) - (a1[1] - a2[1]) * (x3 - x4)
            if d == 0:
                continue
            t = ((a1[0] - x3) * (y3 - y4) - (a1[1] - y3) * (x3 - x4)) / d
            u = -((a1[0] - a2[0]) * (a1[1] - y3) - (a1[1] - a2[1]) * (a1[0] - x3)) / d
            if 0 < t < 1 and 0 < u < 1:
                x = a1[0] + t * (a2[0] - a1[0])
                y = a1[1] + t * (a2[1] - a1[1])
                dx = x - a1[0]
                dy = y - a1[1]
                dist = dx * dx + dy * dy
                yield (dist, i, (x, y))

    # These two, along with a call to adjustSize are required for the
    # scroll area.
    def sizeHint(self):
        return self.minimumSizeHint()
    def minimumSizeHint(self):
        if self.pixmap:
            size = self.scale * self.pixmap.size()
            padding = QtCore.QSize(
                EXTRA_SCROLL_PADDING * 2,
                EXTRA_SCROLL_PADDING * 2
            ) if (self.paddingEnabled and abs(self.scale - 1.0) > 1e-3) else QtCore.QSize(0, 0)
            return size + padding
        return super(Canvas, self).minimumSizeHint()

    def wheelEvent(self, ev):
        if QT5:
            mods = ev.modifiers()
            delta = ev.angleDelta()
            if QtCore.Qt.ControlModifier == int(mods):
                # with Ctrl/Command key
                # zoom
                self.zoomRequest.emit(delta.y(), ev.pos())
            else:
                # scroll
                self.scrollRequest.emit(delta.x(), QtCore.Qt.Horizontal)
                self.scrollRequest.emit(delta.y(), QtCore.Qt.Vertical)
        else:
            if ev.orientation() == QtCore.Qt.Vertical:
                mods = ev.modifiers()
                if QtCore.Qt.ControlModifier == int(mods):
                    # with Ctrl/Command key
                    self.zoomRequest.emit(ev.delta(), ev.pos())
                else:
                    self.scrollRequest.emit(
                        ev.delta(),
                        QtCore.Qt.Horizontal
                        if (QtCore.Qt.ShiftModifier == int(mods))
                        else QtCore.Qt.Vertical)
            else:
                self.scrollRequest.emit(ev.delta(), QtCore.Qt.Horizontal)
        ev.accept()

    def keyPressEvent(self, ev):
        key = ev.key()
        if key == QtCore.Qt.Key_Insert:
            if self.selectedEdge is not None:
                self.addPointToEdge()
                self.storeShapes()
                self.shapeMoved.emit()
                self.edgeSelected.emit(False)
                self.vertexSelected.emit(True)
                ev.accept()
                return
        if key == QtCore.Qt.Key_Delete:
            if self.deleteSelectedPoint():
                self.shapeMoved.emit()
                self.vertexSelected.emit(False)
                ev.accept()
                return
        if key == QtCore.Qt.Key_Escape and self.current:
            self.current = None
            self.drawingPolygon.emit(False)
            self.update()
            return
        elif key == QtCore.Qt.Key_Return and self.canCloseShape():
            self.finalise()
            return

        super(Canvas, self).keyPressEvent(ev)

    def setLastLabel(self, text, flags):
        assert text
        self.shapes[-1].label = text
        self.shapes[-1].flags = flags
        self.shapesBackups.pop()
        self.storeShapes()
        return self.shapes[-1]

    def undoLastLine(self):
        assert self.shapes
        self.current = self.shapes.pop()
        self.current.setOpen()
        if self.createMode in ['polygon', 'linestrip']:
            self.line.points = [self.current[-1], self.current[0]]
        elif self.createMode in ['rectangle', 'line', 'circle']:
            self.current.points = self.current.points[0:1]
        elif self.createMode == 'point':
            self.current = None
        self.drawingPolygon.emit(True)

    def undoLastPoint(self):
        if not self.current or self.current.isClosed():
            return
        self.current.popPoint()
        if len(self.current) > 0:
            self.line[0] = self.current[-1]
        else:
            self.current = None
            self.drawingPolygon.emit(False)
        self.repaint()

    def loadPixmap(self, pixmap):
        self.pixmap = pixmap
        self.shapes = []
        self.referencePoint = None
        self.repaint()

    def loadShapes(self, shapes, replace=True):
        if replace:
            self.shapes = list(shapes)
        else:
            self.shapes.extend(shapes)
        self.storeShapes()
        self.current = None
        self.repaint()

    def setShapeVisible(self, shape, value):
        self.visible[shape] = value
        self.repaint()

    def overrideCursor(self, cursor):
        self.restoreCursor()
        self._cursor = cursor
        QtWidgets.QApplication.setOverrideCursor(cursor)

    def restoreCursor(self):
        QtWidgets.QApplication.restoreOverrideCursor()

    def resetState(self):
        self.restoreCursor()
        self.pixmap = None
        self.shapesBackups = []
        self.disarmShapeMove()
        self.stopLongPressMove()
        self.update()

















































