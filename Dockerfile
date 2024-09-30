FROM ubuntu:latest

RUN apt update && apt install -y python3 python3-pip python3-rrdtool

# Install the requirements
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install -r /tmp/requirements.txt --break-system-packages

# Copy the application
COPY main.py /app/main.py
COPY requirements.txt /app/requirements.txt
WORKDIR /app

# Run the application
ENTRYPOINT ["python3", "main.py"]