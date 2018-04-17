# Ansible VCR

A record and playback framework to debug ansible.

Inspired by https://pypi.org/project/vcrpy/

## Purpose
Your inventory and or your playbooks are too large to break down into a simple reproducer. Use this project to "capture" all the data that is sent over ssh to the remote nodes, and then share the captured data with someone who knows how to debug ansible.

## Instructions
The example test.sh script demonstrates how to use this. More documentation will follow as the code is cleaned up and tested.
