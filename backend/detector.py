"""
YOLOv8-nano Object Detection — Runs ONLY when SNN fires.
15fps on CPU. No GPU. No cloud. Just intelligence.
"""

from ultralytics import YOLO


class PersonDetector:
    def __init__(self, model_path="yolov8n.pt", confidence=0.3):
        self.model = YOLO(model_path)
        self.confidence = confidence
        self.person_class_id = 0  # COCO class 0 = person
        self.detection_count = 0
        self.frame_count = 0

    def detect(self, frame):
        """
        Detect objects in frame.
        Returns: list of dicts with box, confidence, class info
        """
        self.frame_count += 1
        results = self.model(frame, conf=self.confidence, verbose=False)

        detections = []
        for result in results:
            for box in result.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                class_name = self.model.names[cls_id]

                detections.append({
                    'box': (x1, y1, x2, y2),
                    'confidence': round(conf, 3),
                    'class_name': class_name,
                    'class_id': cls_id,
                    'is_person': cls_id == self.person_class_id,
                    'area': (x2 - x1) * (y2 - y1),
                    'center': ((x1 + x2) // 2, (y1 + y2) // 2)
                })

        self.detection_count += len(detections)
        return detections

    def get_person_count(self, detections):
        return sum(1 for d in detections if d['is_person'])

    def get_max_confidence(self, detections):
        if not detections:
            return 0.0
        return max(d['confidence'] for d in detections)
