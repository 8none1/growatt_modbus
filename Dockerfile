# Use the official Python base image
FROM python:3.12-slim
#FROM cgr.dev/chainguard/python:latest-dev

# Set working directory
WORKDIR /app

# Copy the requirements file
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy your script
COPY growatt_modbus.py .

# Define the command to run the script
CMD ["python", "growatt_modbus.py"]
