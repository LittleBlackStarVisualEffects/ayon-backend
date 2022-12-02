#!/bin/bash

echo "Starting the backend"
python -m setup --ensure-installed 

while true; do
    uvicorn --log-level info --host 0.0.0.0 --port 5000 openpype.api:app
    return_code=$?
    if [ $return_code -eq 0 ]; then
        echo "Server exited with code 0. Respawning..." >&2
        continue
    fi
    echo "Server terminated with code $return_code. Waiting before respawn." >&2
    sleep 5
done
