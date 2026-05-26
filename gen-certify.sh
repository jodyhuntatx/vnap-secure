#!/bin/bash

OUTPUT_DIR=/vnap-secure/vnap-certs/certify

# This script must be executed in an instance of a vnap:latest image.
# It uses vanetza certify tool to generate PKI authority creds.

main() {
    cd $OUTPUT_DIR
    rm ./*
    gen-native
}

gen-native() {
    ########################################### 
    # certify generate-key
    # Available options:
    #   --help                Print out available options.
    #   --output arg          Output file.
    echo "Generating root signing key..."
    certify generate-key --output ./root_ca_vnap.key

    # certify generate-root:
    # Available options:
    #   --help                                Print out available options.
    #   --output arg                          Output file.
    #   --subject-key arg                     Private key file.
    #   --subject-name arg (=Hello World Root-CA)
    #                                         Subject name.
    #   --days arg (=365)                     Validity in days.
    #   --aid arg                             Allowed ITS-AIDs to restrict 
    #                                         permissions, defaults to 36 (CA) and 37
    #                                         (DEN) if empty.
    echo "Generating root CA cert..."
    certify generate-root --output ./root_ca_vnap.cert \
                          --subject-key ./root_ca_vnap.key \
                          --subject-name "Test root CA" \
                          --aid 36 --aid 37 --aid 141

    ########################################### 
    # Generate AA signing key & cert
    echo "Generating AA signing key..."
    certify generate-key --output ./aa_vnap.key

    # certify generate-aa
    # 
    # Available options:
    #   --help                                Print out available options.
    #   --output arg                          Output file.
    #   --sign-key arg                        Private key file of the signer.
    #   --sign-cert arg                       Private certificate file of the signer.
    #   --subject-key arg                     Private key file to issue the 
    #                                         certificate for.
    #   --subject-name arg (=Hello World Auth-CA)
    #                                         Subject name.
    #   --days arg (=180)                     Validity in days.
    #   --aid arg                             Allowed ITS-AIDs to restrict 
    #                                         permissions, defaults to 36 (CA) and 37
    #                                        (DEN) if empty.
    echo "Generating AA cert..."
    certify generate-aa --output ./aa_vnap.cert \
                          --sign-key ./root_ca_vnap.key \
                          --sign-cert ./root_ca_vnap.cert \
                          --subject-key ./aa_vnap.key \
                          --subject-name "Authorization Authority" \
                          --aid 36 --aid 37 --aid 141


    ###########################################
    # Generate ITS ticket signing key & cert
    echo "Generating ticket signing key..."
    certify generate-key --output ./ticket_vnap.key

    # certify generate-ticket
    # 
    # Available options:
    #   --help                 Print out available options.
    #   --output arg           Output file.
    #   --sign-key arg         Private key file of the signer.
    #   --sign-cert arg        Private certificate file of the signer.
    #   --subject-key arg      Private key file to issue the certificate for.
    #   --days arg (=7)        Validity in days.
    #   --cam-permissions arg  CAM permissions as binary string (e.g. 
    #                          '1111111111111100' to grant all SSPs)
    #   --denm-permissions arg DENM permissions as binary string (e.g. 
    #                          '000000000000000000000000' to grant no SSPs)
    #   --permit-gn-mgmt       Generated ticket can be used to sign GN-MGMT messages 
    #                          (e.g. beacons). If used, root & AA certs must be
    #                          generated w/ --aid 141
    echo "Generating ticket cert..."
    certify generate-ticket \
                        --output ./ticket_vnap.cert \
                        --subject-key ./ticket_vnap.key \
                        --sign-key ./aa_vnap.key \
                        --sign-cert ./aa_vnap.cert \
                        --cam-permissions '1111111111111100' \
                        --denm-permissions '000000000000000000000000'
}

main "$@"
