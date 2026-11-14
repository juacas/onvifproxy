FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn httpx pyyaml scipy

COPY onvif_fov_proxy.py /app/onvif_fov_proxy.py
COPY fov_math.py /app/fov_math.py
COPY onvif_security.py /app/onvif_security.py
COPY optical_calibration.py /app/optical_calibration.py
COPY calibration.html /app/calibration.html

CMD ["python", "/app/onvif_fov_proxy.py"]
