FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends systemd systemd-sysv python3 util-linux git passwd && rm -rf /var/lib/apt/lists/*
COPY commander_gym /opt/fixture/commander_gym
COPY scripts /opt/fixture/scripts
COPY tests /opt/fixture/tests
RUN chmod -R go-w /opt/fixture && touch /opt/fixture/ISOLATED_RELEASE_TEST
ENV container=docker
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
