"""
UUIDv7 (RFC 9562) utility helper.
Generates 128-bit time-ordered UUIDs with millisecond-precision Unix timestamps.
"""
import os
import time
import uuid

def generate_uuid7() -> str:
    """
    Generate an RFC 9562 compliant UUIDv7 string.

    Layout (128 bits):
    - unix_ts_ms (48 bits): Milliseconds since Unix epoch (time-ordered, k-sortable)
    - ver (4 bits): 0b0111 (UUID version 7)
    - rand_a (12 bits): Pseudorandom bits
    - var (2 bits): 0b10 (RFC 4122/9562 variant 1)
    - rand_b (62 bits): Pseudorandom bits

    Example: '01923e4a-9b1c-7f52-87ad-d84e921d7b14'
    """
    ms = int(time.time() * 1000)
    rand = os.urandom(10)
    b = bytearray(16)
    # 48-bit timestamp in big-endian
    b[0:6] = ms.to_bytes(6, byteorder="big")
    # 4-bit version 7 + 12-bit random (rand[0] & 0x0F, rand[1])
    b[6] = 0x70 | (rand[0] & 0x0F)
    b[7] = rand[1]
    # 2-bit variant 1 (0b10) + 6-bit random (rand[2] & 0x3F)
    b[8] = 0x80 | (rand[2] & 0x3F)
    # 56-bit random
    b[9:16] = rand[3:10]
    return str(uuid.UUID(bytes=bytes(b)))
