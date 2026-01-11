FROM python:alpine

RUN apk add git

RUN pip install git+https://github.com/dakhnod/FPC2534-python.git
RUN pip install git+https://github.com/sbtinstruments/asyncio-mqtt

CMD ["quart", "--app", "fpc2534.quart_app", "run", "--host", "0.0.0.0"]