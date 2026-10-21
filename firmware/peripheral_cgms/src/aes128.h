#ifndef AES128_H
#define AES128_H

/*
 * Minimal AES-128, encrypt direction only (FIPS-197) - all the Dexcom G6 challenge hash needs.
 * Plain C, no Zephyr, no heap, so it is built unchanged on the PC for the host tests
 * (firmware/peripheral_cgms/tests/host). Not constant-time: it protects a simulator's challenge,
 * not a secret worth attacking.
 */

#include <stdint.h>

void aes128_encrypt_block(const uint8_t key[16], const uint8_t in[16], uint8_t out[16]);

#endif /* AES128_H */
