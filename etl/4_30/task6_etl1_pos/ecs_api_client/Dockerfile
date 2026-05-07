FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir requests
COPY app.py .
CMD ["python", "app.py"]
