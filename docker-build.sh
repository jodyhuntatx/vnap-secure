#!/bin/bash

# Adding any argument to the script will copy original versions of the source
# code to the VNAP_REPO to restore the original repo state.

VNAP_REPO=~/vanetza-nap

main() {
  if [[ "$1" != "" ]]; then
    cp_origs
  else
    cp_patches
  fi
  pushd $VNAP_REPO
  docker build --network=host -t vnap:latest .
}

cp_origs() {
  echo "Copying orginal source files to vanetza-nap..."
  cp vnap-origs/basic_header.cpp $VNAP_REPO/vanetza/geonet
  cp vnap-origs/common_header.cpp $VNAP_REPO/vanetza/geonet
  cp vnap-origs/secured_message.cpp $VNAP_REPO/vanetza/security
  cp vnap-origs/ecdsa256.cpp $VNAP_REPO/vanetza/security
  cp vnap-origs/backend_cryptopp.hpp $VNAP_REPO/vanetza/security
  cp vnap-origs/backend_cryptopp.cpp $VNAP_REPO/vanetza/security
}

cp_patches() {
  echo "Copying patched source files to vanetza-nap..."
  # altered socktap entrypoint w/ --security certs & cert/key args
  cp vnap-patches/entrypoint.sh $VNAP_REPO
  # turn on 'certify' build
  cp vnap-patches/CMakeLists.txt $VNAP_REPO
  cp vnap-patches/Dockerfile $VNAP_REPO
  # fix header parsing
  cp vnap-patches/common_header.cpp $VNAP_REPO/vanetza/geonet
  cp vnap-patches/basic_header.cpp $VNAP_REPO/vanetza/geonet
  cp vnap-patches/secured_message.cpp $VNAP_REPO/vanetza/security
  # fix LRU cache core dump
  cp vnap-patches/ecdsa256.cpp $VNAP_REPO/vanetza/security
  cp vnap-patches/backend_cryptopp.hpp $VNAP_REPO/vanetza/security
  cp vnap-patches/backend_cryptopp.cpp $VNAP_REPO/vanetza/security
}

main "$@"
