#!/bin/bash
echo "Hostname: $(hostname)"
SSHUTTLE_COUNT=$(ps -ef | grep sshuttle | grep -v grep | wc -l | sed 's/^[[:space:]]*//g')
echo "sshuttle-process-count:${SSHUTTLE_COUNT}"
