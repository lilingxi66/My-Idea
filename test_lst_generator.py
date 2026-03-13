import os
import unittest
import shutil

class TestLstGenerator(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory structure for testing
        self.root_dir = "test_root"
        self.image_dir = os.path.join(self.root_dir, "dataset/images")
        self.edge_dir = os.path.join(self.root_dir, "dataset/edges")
        self.save_dir = "test_output_dir"
        
        for d in [self.image_dir, self.edge_dir, self.save_dir]:
            if not os.path.exists(d):
                os.makedirs(d)
        
        # Create some matched files
        self.files = ["file1.jpg", "file2.jpg", "sub/file3.jpg"]
        for f_rel in self.files:
            img_path = os.path.join(self.image_dir, f_rel)
            edge_path = os.path.join(self.edge_dir, f_rel.replace(".jpg", ".png"))
            
            os.makedirs(os.path.dirname(img_path), exist_ok=True)
            os.makedirs(os.path.dirname(edge_path), exist_ok=True)
            
            with open(img_path, "w") as f: f.write("img")
            with open(edge_path, "w") as f: f.write("edge")

    def tearDown(self):
        # Clean up temporary test data
        for d in [self.root_dir, self.save_dir]:
            if os.path.exists(d):
                shutil.rmtree(d)

    def test_logic_recursive(self):
        from lst_generator import generate_list_file
        
        filename = "test_recursive.lst"
        success, message = generate_list_file(self.image_dir, self.edge_dir, self.root_dir, self.save_dir, filename, recursive=True)
        
        self.assertTrue(success)
        output_path = os.path.join(self.save_dir, filename)
        
        with open(output_path, "r") as f:
            lines = [line.strip() for line in f.readlines()]
        
        # Expected relative paths to root_dir
        # image: dataset/images/file1.jpg
        # edge: dataset/edges/file1.png
        self.assertEqual(len(lines), 3)
        self.assertIn("dataset/images/file1.jpg dataset/edges/file1.png", lines)
        self.assertIn("dataset/images/sub/file3.jpg dataset/edges/sub/file3.png", lines)

if __name__ == "__main__":
    unittest.main()
