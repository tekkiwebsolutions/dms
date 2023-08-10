FROM python:3.8

# The enviroment variable ensures that the python output is set straight
# to the terminal with out buffering it first
ENV PYTHONUNBUFFERED 1

# create root directory for our project in the container
RUN mkdir /app

# Set the working directory to /app
WORKDIR /app

# install tessaract and dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends tesseract-ocr
RUN apt-get install -y --no-install-recommends poppler-utils
RUN tesseract --version

RUN pip install --upgrade pip
RUN pip install virtualenv

# Install and enable venv
RUN virtualenv venv
RUN /bin/bash -c "source venv/bin/activate"

# Copy the current directory contents into the container at /app
ADD . /app/

# Install any needed packages specified in requirements.txt
RUN pip install -r development.txt
RUN pip install -r base.txt

# Apply migrations and create superuser
RUN python manage.py migrate
RUN python manage.py createsuperuser --noinput \
    --username=admin --email=admin@example.com

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]



