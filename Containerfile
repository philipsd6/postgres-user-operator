FROM python:3.11-slim-bookworm
ADD pguser.py requirements.txt /src
WORKDIR /src
ENV PIP_ROOT_USER_ACTION=ignore
RUN pip install --upgrade pip
RUN pip install -r requirements.txt
CMD ["kopf", "run", "pguser.py", "--all-namespaces"]
