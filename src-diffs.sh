#!/bin/bash

printf '#%.0s' $(seq 1 $(tput cols))
echo; echo "Build mods:"
printf '=%.0s' $(seq 1 $(tput cols))
echo; echo "### CMakeLists.txt: Turn BUILD_CERTIFY on:"
sdiff -s vnap-origs/CMakeLists.txt vnap-patches/CMakeLists.txt
printf '=%.0s' $(seq 1 $(tput cols))
echo; echo "### Dockerfile: Trigger certify build & copy to /usr/bin:"
sdiff -s vnap-origs/Dockerfile vnap-patches/Dockerfile
printf '=%.0s' $(seq 1 $(tput cols))
echo; echo "### entrypoint.sh: Add logic to run with security=[certs | none]"
sdiff -s vnap-origs/entrypoint.sh vnap-patches/entrypoint.sh
printf '=%.0s' $(seq 1 $(tput cols))

echo
printf '#%.0s' $(seq 1 $(tput cols))
echo; echo "Parser fixes:"
printf '=%.0s' $(seq 1 $(tput cols))
echo; echo "### security/secured_message.cpp: Uncomment protocol version parsing:"
sdiff -s vnap-origs/secured_message.cpp vnap-patches/secured_message.cpp
printf '=%.0s' $(seq 1 $(tput cols))
echo; echo "### security/basic_header.cpp: Delete redundant secure header parsing:"
sdiff -s vnap-origs/basic_header.cpp vnap-patches/basic_header.cpp
printf '=%.0s' $(seq 1 $(tput cols))
echo; echo "### security/common_header: Read first byte from archive:"
sdiff -s vnap-origs/common_header.cpp vnap-patches/common_header.cpp

echo
printf '#%.0s' $(seq 1 $(tput cols))
echo; echo "LRU cache fixes:"
printf '=%.0s' $(seq 1 $(tput cols))
echo; echo "### security/backend_cryptopp.hpp: Mutex defined to protect cache access:"
sdiff -s vnap-origs/backend_cryptopp.hpp vnap-patches/backend_cryptopp.hpp
printf '=%.0s' $(seq 1 $(tput cols))
echo; echo "### security/backend_cryptopp.cpp: Mutex implemented to protect cache access:"
sdiff -s vnap-origs/backend_cryptopp.cpp vnap-patches/backend_cryptopp.cpp
printf '=%.0s' $(seq 1 $(tput cols))
echo; echo "### security/ecdsa256.cpp: Make public key hash deterministic:"
sdiff -s vnap-origs/ecdsa256.cpp vnap-patches/ecdsa256.cpp

echo
printf '#%.0s' $(seq 1 $(tput cols))
echo; echo "Certificate validation:" 
printf '=%.0s' $(seq 1 $(tput cols))
echo; echo "### tools/socktap/main.cpp: Turn on strict certificate validation when security=certs, only use Non_Strict when security=none"
sdiff -s vnap-origs/main.cpp vnap-patches/main.cpp

printf '=%.0s' $(seq 1 $(tput cols))
