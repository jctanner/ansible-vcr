#!/bin/bash
export SSH_AUTH_SOCK=0
VERSION=$(ansible --version | head -n1 | awk '{print $2}')
PLAYBOOK=site.yml
#VMODE="-v"
VMODE="-vvvv"


export ANSIBLE_CALLBACK_WHITELIST=vcr


rm -rf ansible.log
rm -rf /tmp/fixtures

echo "#### STARTING RECORD MODE ..."
ANSIBLE_VCR_MODE="record" ansible-playbook $VMODE -i inventory $PLAYBOOK
RC=$?
if [[ $RC != 0 ]]; then
    exit $RC
fi

#exit 1

echo "#### WAITING 5 SECONDS ..."
sleep 5
rm -rf ansible.log
rm -rf /tmp/fixtures/callback*
rm -rf /tmp/fixtures/fixture_read.log

echo "#### STARTING PLAY MODE ..."
ANSIBLE_VCR_MODE="play" ANSIBLE_KEEP_REMOTE_FILES=1 ansible-playbook $VMODE -i inventory $PLAYBOOK

RC=$?
exit $RC
