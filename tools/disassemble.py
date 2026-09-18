"""Disassemble a local loaded-image snapshot at Xbox virtual addresses (Capstone)."""
import argparse
from pathlib import Path
import capstone

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('image', type=Path)
parser.add_argument('addresses', nargs='+', type=lambda s: int(s, 0))
parser.add_argument('--base', type=lambda s: int(s, 0), default=0x82000000)
parser.add_argument('--bytes', type=int, default=128)
args = parser.parse_args()
data = args.image.read_bytes()
decoder = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_32 | capstone.CS_MODE_BIG_ENDIAN)
for address in args.addresses:
    offset = address - args.base
    if not 0 <= offset < len(data):
        parser.error(f'Address outside snapshot: {address:#x}')
    print(f'Address {address:08X}')
    for instruction in decoder.disasm(data[offset:offset + args.bytes], address):
        print(f'{instruction.address:08X}  {instruction.bytes.hex():8s}  {instruction.mnemonic:10s} {instruction.op_str}')
