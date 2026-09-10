# The loader agent: docker CLI, jq, and one script.
#
# Deliberately tiny. This is the only container in the deployment with access to
# the Docker socket, so everything about it should be easy to read in full
# before you decide to run it — see agent.sh for what it will and will not do.
FROM docker:cli

RUN apk add --no-cache jq coreutils bzip2

COPY agent.sh /usr/local/bin/agent.sh
RUN chmod +x /usr/local/bin/agent.sh

ENTRYPOINT ["/usr/local/bin/agent.sh"]
