FROM python:3.11-slim
WORKDIR /pcqv
COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "artifact/verify_final_claims.py"]
