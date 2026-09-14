# Tech Stack & Technologies Used

> [!NOTE]
> This document details all the tools, frameworks, libraries, and technologies used to build the Sentinel Gujarat Platform.

The Sentinel Gujarat platform is designed to be highly performant, utilizing modern async frameworks, hardware-accelerated AI pipelines, and a lightweight, dependency-free frontend.

## 1. Backend Engineering
- **[Python (3.11 - 3.14)](https://python.org)**: Core programming language used for the backend server and AI orchestration.
- **[FastAPI (0.115+)](https://fastapi.tiangolo.com/)**: High-performance asynchronous web framework used for the REST API and WebSocket Hub.
- **[Uvicorn](https://www.uvicorn.org/)**: ASGI web server implementation for Python used to run FastAPI.
- **AsyncIO**: Python's built-in asynchronous I/O framework used extensively for handling simultaneous video proxying and concurrent API requests.
- **Pydantic**: Data validation and settings management using Python type annotations. Used for API schemas and environment variable validation.
- **Httpx**: Fully featured asynchronous HTTP client for Python, used heavily in the Server-Side HLS Proxy.

## 2. Artificial Intelligence & Computer Vision
- **[PyTorch](https://pytorch.org/)**: Deep learning tensor library providing GPU-accelerated tensor computation. Used as the foundation for the AI models.
- **[Ultralytics YOLO (YOLOv8 / YOLO26)](https://ultralytics.com/)**: State-of-the-art, real-time object detection model. Configured specifically to detect vehicles (cars, trucks, buses, motorcycles).
- **ByteTrack**: Highly robust multi-object tracking algorithm used to assign persistent IDs to vehicles across frames.
- **[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)**: High-accuracy, multilingual Optical Character Recognition engine used for the ANPR (Automatic Number Plate Recognition) pipeline.
- **[OpenCV (cv2)](https://opencv.org/)**: Industry-standard computer vision library used for RTSP stream ingestion, decoding video frames, and manipulating image matrices (cropping, grayscaling, thresholding).

## 3. Database & ORM
- **[PostgreSQL](https://www.postgresql.org/)**: Primary relational database for production persistence.
- **PostGIS**: Spatial database extender for PostgreSQL, used for advanced geographical queries on camera nodes and vehicle sightings.
- **SQLite**: Lightweight database used as an out-of-the-box fallback for the PoC (Proof of Concept) and rapid local development.
- **SQLAlchemy**: The Python SQL toolkit and Object Relational Mapper (ORM) used to interact with the database.
- **Alembic**: Lightweight database migration tool for use with SQLAlchemy.

## 4. Frontend & User Interface
- **Vanilla JavaScript (ES6+)**: The entire dashboard logic is written in plain JavaScript to ensure ultra-fast load times and zero framework overhead.
- **HTML5 & CSS3**: Native markup and styling utilizing CSS Grid and Flexbox for the responsive surveillance dashboard.
- **[hls.js](https://github.com/video-dev/hls.js)**: JavaScript library that implements an HTTP Live Streaming (HLS) client. Used to render the proxied AES-128 encrypted CCTV streams natively in the browser without plugins.
- **[Leaflet.js](https://leafletjs.com/)**: Open-source JavaScript library for mobile-friendly interactive maps. Used to render the GIS dashboard and plot vehicle journeys.

## 5. Streaming Protocols & Networking
- **RTSP (Real Time Streaming Protocol)**: Used for ingesting raw video from the Sentinel sandbox (forced over TCP for reliability).
- **HLS (HTTP Live Streaming)**: Used for delivering low-latency, encrypted video to the browser.
- **WebRTC / WHEP**: WebRTC HTTP Egress Protocol used for sub-200ms ultra-low latency viewing.
- **WebSockets**: Bi-directional communication channel used to instantly push `HIGH` severity watchlist alerts from the AI backend directly to the operator's screen.

## 6. Security & Authentication
- **JWT (JSON Web Tokens)**: Industry standard used for stateless user authentication.
- **Bcrypt / Passlib**: Strong cryptographic hash functions used for securely hashing operator passwords in the database.
- **AES-128 (Advanced Encryption Standard)**: Symmetric encryption algorithm utilized by the upstream Sentinel HLS CDN and securely proxied by the backend.

## 7. Deployment & Infrastructure
- **[Docker & Docker Compose](https://www.docker.com/)**: Containerization tools used to easily spin up the database layer (PostgreSQL + PostGIS).
- **Gunicorn**: Python WSGI HTTP Server for UNIX, used as a process manager for Uvicorn workers in production.
- **Nginx**: High-performance reverse proxy used for SSL/TLS termination and load balancing.

## 8. External Integrations
- **[OSRM (Open Source Routing Machine)](http://project-osrm.org/)**: Used to calculate and infer the shortest road path between camera coordinates for vehicle tracking.
- **Google Maps API**: Optional integration for highly accurate road corridor mapping and polyline generation.
