"""Strict RPC-to-bytes encoding for the supported anchored-evidence profile."""

from traze_evidence.crypto import keccak256, rlp_encode, transaction_root
from traze_evidence.errors import require
from traze_evidence.secure_trie import hex_bytes, rpc_quantity
from traze_evidence.verify import decode_header


HEADER_FIELDS = (
    ('parentHash', 32), ('sha3Uncles', 32), ('miner', 20), ('stateRoot', 32),
    ('transactionsRoot', 32), ('receiptsRoot', 32), ('logsBloom', 256),
    ('difficulty', None), ('number', None), ('gasLimit', None), ('gasUsed', None),
    ('timestamp', None), ('extraData', 0), ('mixHash', 32), ('nonce', 8),
    ('baseFeePerGas', None), ('withdrawalsRoot', 32), ('blobGasUsed', None),
    ('excessBlobGas', None), ('parentBeaconBlockRoot', 32), ('requestsHash', 32),
)


def header_row(block):
    require(isinstance(block, dict) and all(key in block and block[key] is not None for key, _ in HEADER_FIELDS)
            and block.get('blockAccessListHash') is None,
            'hemi_header_format', 'The source header is outside the supported 21-field profile', incomplete=True)
    fields = [rpc_quantity(block[key]) if width is None else hex_bytes(block[key], size=width or None, limit=2048)
              for key, width in HEADER_FIELDS]
    row = {'number': rpc_quantity(block['number'], bits=64), 'hash': block['hash'].lower(),
           'raw': rlp_encode(fields).hex()}
    decode_header(row)
    return row


def transaction_bytes(tx):
    require(isinstance(tx, dict), 'hemi_transaction_format', 'A full transaction object is required')
    kind = rpc_quantity(tx.get('type', '0x0'), bits=8)
    q = lambda key: rpc_quantity(tx.get(key, '0x0'))
    address = hex_bytes(tx.get('to') or '0x', limit=20)
    require(len(address) in (0, 20), 'hemi_transaction_format', 'Invalid transaction recipient')
    data = hex_bytes(tx.get('input', '0x'), limit=2_000_000)
    access = tx.get('accessList', [])
    require(isinstance(access, list) and len(access) <= 4096, 'hemi_transaction_format', 'Invalid access list')
    access = [[hex_bytes(row['address'], size=20), [hex_bytes(key, size=32) for key in row['storageKeys']]]
              for row in access]
    parity = q('yParity') if tx.get('yParity') is not None else q('v')
    if kind == 0:
        values = [q('nonce'), q('gasPrice'), q('gas'), address, q('value'), data, q('v'), q('r'), q('s')]
    elif kind == 1:
        values = [q('chainId'), q('nonce'), q('gasPrice'), q('gas'), address, q('value'), data,
                  access, parity, q('r'), q('s')]
    elif kind in (2, 3, 4):
        values = [q('chainId'), q('nonce'), q('maxPriorityFeePerGas'), q('maxFeePerGas'),
                  q('gas'), address, q('value'), data, access]
        if kind == 3:
            values += [q('maxFeePerBlobGas'), [hex_bytes(value, size=32) for value in tx['blobVersionedHashes']]]
        if kind == 4:
            values += [[[hex_bytes(row[key], size=20) if key == 'address' else rpc_quantity(row[key])
                         for key in ('chainId', 'address', 'nonce', 'yParity', 'r', 's')]
                        for row in tx['authorizationList']]]
        values += [parity, q('r'), q('s')]
    elif kind in (0x7c, 0x7d):
        values = [address, q('gas'), data]
    elif kind == 0x7e:
        flag = tx.get('isSystemTx', False)
        flag = int(flag) if isinstance(flag, bool) else rpc_quantity(flag, bits=8)
        require(flag in (0, 1), 'hemi_transaction_format', 'Invalid system-transaction flag')
        values = [hex_bytes(tx['sourceHash'], size=32), hex_bytes(tx['from'], size=20), address,
                  q('mint'), q('value'), q('gas'), flag, data]
    else:
        require(False, 'hemi_transaction_type', 'A transaction uses an unsupported encoding', incomplete=True)
    raw = (b'' if kind == 0 else bytes([kind])) + rlp_encode(values)
    require(keccak256(raw) == hex_bytes(tx['hash'], size=32),
            'hemi_transaction_hash', 'Reconstructed full transaction hash differs')
    return raw


def body_row(block, check=lambda: None):
    header = header_row(block)
    txs = block.get('transactions')
    require(isinstance(txs, list) and 0 < len(txs) <= 4096,
            'hemi_body_format', 'A bounded complete transaction body is required', incomplete=True)
    values = []
    for index, tx in enumerate(txs):
        check()
        require(tx.get('blockHash', '').lower() == header['hash']
                and rpc_quantity(tx['blockNumber'], bits=64) == header['number']
                and rpc_quantity(tx['transactionIndex'], bits=64) == index,
                'hemi_transaction_identity', 'Transaction metadata belongs to another block or position')
        values.append(transaction_bytes(tx))
    check()
    require(transaction_root(values) == hex_bytes(block['transactionsRoot'], size=32),
            'hemi_transaction_root', 'Reconstructed complete Hemi transaction root differs')
    return {'number': header['number'], 'transactions': [value.hex() for value in values]}
