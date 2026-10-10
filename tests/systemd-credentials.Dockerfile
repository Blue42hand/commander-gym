FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends systemd systemd-sysv python3 util-linux && rm -rf /var/lib/apt/lists/*
COPY commander_gym /opt/fixture/commander_gym
COPY tests /opt/fixture/tests
RUN chmod -R go-w /opt/fixture && useradd -u 10001 native-qa && useradd -u 10002 sidecar-qa && useradd -u 10003 unrelated-qa
ENV container=docker
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
