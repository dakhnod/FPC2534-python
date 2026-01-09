FROM python:alpine

RUN apk add git

RUN pip install git+https://github.com/dakhnod/FPC2534-python.git

CMD ["quart", "run"]