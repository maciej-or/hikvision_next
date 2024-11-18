FROM python:3.12
ENV PIP_DISABLE_ROOT_WARNING=1
RUN python -m pip install --upgrade pip
COPY requirements.test.txt requirements.txt
RUN pip install -r requirements.txt
RUN pip install debugpy
VOLUME /app
WORKDIR /app
ENV NO_COLOR=yes_please
ENV LANG=C
CMD ["python", "-m", "pytest"]