FROM ubuntu:latest

RUN apt update && apt install -y python3 python3-pip python3-rrdtool

RUN pip3 install --upgrade pip

# Install the requirements
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install -r /tmp/requirements.txt

# Copy the application
COPY . /app
WORKDIR /app

# Need to figure out credentials

# Run the application
CMD ["python3", "main.py"]

