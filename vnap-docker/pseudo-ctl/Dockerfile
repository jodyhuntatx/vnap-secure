# Pseudonym change event channel: control broker and event client in one small image.
#   docker run ... vnap-pseudo-ctl broker   -> MQTT broker of the control channel
#   docker run ... vnap-pseudo-ctl client   -> publishes pseudonym change events (see pseudo-ctl.sh)
#   docker exec <client> change <station> [index]   -> one manual event
FROM eclipse-mosquitto:2
COPY pseudo-ctl.sh /usr/local/bin/pseudo-ctl
COPY change.sh /usr/local/bin/change
RUN chmod +x /usr/local/bin/pseudo-ctl /usr/local/bin/change
ENTRYPOINT ["/usr/local/bin/pseudo-ctl"]
CMD ["client"]
