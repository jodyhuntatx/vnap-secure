#!/bin/bash

if [[ $# != 1 ]]; then
  echo "Usage: $0 <path-to-private-key-in-pem-format>"
  exit -1
fi
PKEY=$1
if [[ "$(cat $PKEY | grep 'PRIVATE KEY')" == "" ]]; then
  echo "$PKEY is not a private key in PEM format."
  exit -1
fi

# Format accepted by Vanetza-NAP
openssl pkcs8 -topk8 -nocrypt -in $PKEY -outform DER -out $PKEY.der
openssl pkey -in $PKEY.der -inform DER -text -noout

# Format accepted by Vanetza (riebl)
#openssl ec -in "$PKEY" -outform DER -out $PKEY.der
#openssl ec -inform DER -in $PKEY.der -text -noout
