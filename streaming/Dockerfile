# Flink with Python, the Kafka connector and a Postgres driver: one image for
# the job manager, the task manager and the supervisor that submits the job.
FROM flink:1.20.5-scala_2.12-java17

# Ubuntu 22.04's Python 3.10, which PyFlink 1.20 supports. Flink starts Python
# workers as `python`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/bin/python3 /usr/bin/python

# PyFlink pulls a few hundred MB; a slow mirror should not fail the build.
RUN pip3 install --no-cache-dir --default-timeout=300 --retries 10 "apache-flink==1.20.5" "psycopg[binary]>=3.2,<4"

ADD --chmod=644 \
    https://repo.maven.apache.org/maven2/org/apache/flink/flink-sql-connector-kafka/3.4.0-1.20/flink-sql-connector-kafka-3.4.0-1.20.jar \
    /opt/flink/lib/

COPY window_metrics.py job.py supervise.py /opt/streaming/
ENV PYTHONPATH=/opt/streaming
