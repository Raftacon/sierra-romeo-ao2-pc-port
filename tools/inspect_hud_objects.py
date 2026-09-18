"""Read bounded HUD, menu or engine observations from this build; never write memory.

The running process is not suspended. This is a diagnostic observation of the
retail object table, not an atomic snapshot or proof that an object was drawn.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
from pathlib import Path
import re
import struct
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--virtual-base', type=lambda value: int(value, 0),
                        help='Explicit guest arena base for interactive sessions without a mapping log; retail signature is still verified')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--menu', action='store_true', help='Inspect menu scenes and message-box actions instead of HUD widgets')
    selection.add_argument('--engine', action='store_true', help='Inspect engine objects and raw native render-control words instead of HUD widgets')
    selection.add_argument('--contains', help='Inspect object/class names containing this literal substring')
    parser.add_argument('--function-script', action='store_true',
                        help='Include bounded live Function bytecode and its object references (requires --contains)')
    parser.add_argument('--vtable', action='store_true',
                        help='Include 128 native virtual function slots (requires --contains)')
    parser.add_argument('--sequence-links', action='store_true',
                        help='Decode bounded output links of retail UI/sequence actions (requires --contains)')
    parser.add_argument('--object-strings', action='store_true',
                        help='Observe bounded printable FString candidates in selected objects (requires --contains)')
    parser.add_argument('--include-path', action='store_true',
                        help='Also match the resolved outer path with --contains')
    parser.add_argument('--campaign-network', action='store_true',
                        help='Include bounded native co-op service pointers, vtables and start-event names')
    parser.add_argument('--combat-state', action='store_true',
                        help='Read reflected health, ammo and owner fields; requires --campaign-network')
    parser.add_argument('--object-bytes', type=int, default=0,
                        help='Override raw object read size, 512-4096 bytes in multiples of four')
    args = parser.parse_args()
    if args.combat_state and not args.campaign_network:
        parser.error('--combat-state requires --campaign-network')
    if args.include_path and args.contains is None:
        parser.error('--include-path requires --contains')
    if args.object_bytes and (not 512 <= args.object_bytes <= 4096 or args.object_bytes % 4):
        parser.error('--object-bytes requires 512-4096 bytes in multiples of four')
    if (args.function_script or args.vtable or args.sequence_links or args.object_strings) and args.contains is None:
        parser.error('Detailed function observations require --contains')
    if args.contains is not None and not 3 <= len(args.contains) <= 64:
        parser.error('--contains requires 3-64 characters')
    if args.output.exists():
        parser.error('Use a new output file')
    root = Path(__file__).resolve().parents[1]
    expected = (root / 'out/build/RelWithDebInfo/army_of_two.exe').resolve()
    locator = json.loads((args.probe / 'running.json').read_text())
    if args.virtual_base is not None:
        base = args.virtual_base
        if base <= 0 or base % 0x10000:
            parser.error('--virtual-base must be a positive allocation-aligned address')
    else:
        match = re.search(r'Guest memory arena mapped: virtual base (0x[0-9A-Fa-f]+)',
                          (args.probe / 'runtime.log').read_text(errors='replace'))
        if not match:
            raise RuntimeError('No recorded guest memory mapping')
        base = int(match[1], 16)
    kernel = c.WinDLL('kernel32', use_last_error=True)
    def api(name, result, *arguments):
        fn = getattr(kernel, name)
        fn.restype, fn.argtypes = result, arguments
        return fn
    op = api('OpenProcess', w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
    close = api('CloseHandle', w.BOOL, w.HANDLE)
    query = api('QueryFullProcessImageNameW', w.BOOL, w.HANDLE, w.DWORD,
                w.LPWSTR, c.POINTER(w.DWORD))
    rpm = api('ReadProcessMemory', w.BOOL, w.HANDLE, c.c_void_p,
              c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t))
    handle = op(0x1000 | 0x10, False, locator['pid'])
    if not handle:
        raise c.WinError(c.get_last_error())
    started, read_bytes, failures = time.monotonic(), 0, 0
    failed_ranges = []
    read_phase = 'object_inventory'
    try:
        image, length = c.create_unicode_buffer(32768), w.DWORD(32768)
        if not query(handle, 0, image, c.byref(length)) or Path(image.value).resolve() != expected:
            raise RuntimeError('PID is not this project native executable')
        def read(address, size):
            nonlocal read_bytes, failures
            if (not 0 < address < 0x100000000 or not 0 < size <= 4 * 1024 * 1024
                    or address + size > 0x100000000):
                raise ValueError('Invalid guest read')
            if read_bytes + size > 64 * 1024 * 1024 or time.monotonic() - started > 20:
                raise RuntimeError('HUD inventory read budget exceeded')
            read_bytes += size
            data, received = c.create_string_buffer(size), c.c_size_t()
            if not rpm(handle, base + address, data, size, c.byref(received)) or received.value != size:
                failures += 1
                if len(failed_ranges) < 32:
                    failed_ranges.append({'guest_address': hex(address), 'requested_bytes': size,
                                          'received_bytes': received.value, 'win32_error': c.get_last_error(),
                                          'phase': read_phase})
                raise ValueError('Unreadable guest range')
            return data.raw
        def word(address):
            return struct.unpack('>I', read(address, 4))[0]
        # Validate the revision-sensitive FName routine before using its globals.
        pinned = bytes.fromhex('7d8802a69181fff8fbe1fff09421ffa0')
        if read(0x824340D0, 16) != pinned:
            raise RuntimeError('Unexpected retail FName routine')
        names, name_count = struct.unpack('>II', read(0x83101004, 8))
        objects, object_count = struct.unpack('>II', read(0x8311471C, 8))
        if not 0 < name_count <= 500000 or not 0 < object_count <= 500000:
            raise RuntimeError('Unexpected retail table bounds')
        name_pointers = struct.unpack(f'>{name_count}I', read(names, name_count * 4))
        object_pointers = struct.unpack(f'>{object_count}I', read(objects, object_count * 4))
        name_cache = {}
        def name(index):
            if index >= name_count:
                return '<invalid>'
            if index not in name_cache:
                # FName entries have an ASCII name at +0x10. Read a bounded
                # prefix: only class and asset names are needed for this survey.
                value = read(name_pointers[index] + 0x10, 128).split(b'\0', 1)[0]
                name_cache[index] = value.decode('ascii', errors='replace')
            return name_cache[index]
        selected = []
        pointer_set = set(object_pointers)
        def describe(pointer):
            data = read(pointer, 0x38)
            index, number, cls = struct.unpack_from('>III', data, 0x2C)
            parts, seen = [name(index)], {pointer}
            outer = struct.unpack_from('>I', data, 0x28)[0]
            while outer and outer in pointer_set and outer not in seen and len(parts) < 12:
                seen.add(outer)
                parts.append(name(word(outer + 0x2C)))
                outer = word(outer + 0x28)
            return {'object': hex(pointer), 'name': name(index), 'name_number': number,
                    'path': '.'.join(reversed(parts)),
                    'class': name(word(cls + 0x2C)) if cls else '',
                    'vtable': hex(struct.unpack_from('>I', data)[0])}
        combat_specs = {
            'Engine.Pawn.Health': 'IntProperty',
            'UTGame.AO2Pawn.DefaultHealth': 'IntProperty',
            'UTGame.UTWeapon.AmmoCount': 'IntProperty',
            'UTGame.UTWeapon.MaxAmmoCount': 'IntProperty',
            'AO2Core.AO2Weapon.ReserveAmmoCount': 'IntProperty',
            'AO2Core.AO2Weapon.MaxReserveAmmoCount': 'IntProperty',
            'Engine.Actor.Owner': 'ObjectProperty',
        }
        combat_names = {p.rsplit('.',1)[1] for p in combat_specs}
        combat_properties = {}
        for pointer in object_pointers:
            if not pointer:
                continue
            try:
                data = read(pointer, 0x38)
                index, number, cls = struct.unpack_from('>III', data, 0x2C)
                object_name = name(index)
                class_name = name(word(cls + 0x2C)) if cls else ''
                if args.combat_state and class_name in ('IntProperty', 'ObjectProperty') and object_name in combat_names:
                    path = describe(pointer)['path']
                    if path in combat_specs:
                        dimension, size = struct.unpack('>II', read(pointer+0x44,8))
                        offset = word(pointer+0x64)
                        if class_name != combat_specs[path] or dimension != 1 or size != 4 or not 0x38 <= offset <= 0x4000:
                            raise RuntimeError('Unexpected combat property layout: '+path)
                        if path in combat_properties:
                            raise RuntimeError('Duplicate combat property: '+path)
                        combat_properties[path] = dict(object=hex(pointer), type=class_name, offset=offset)
                wanted = class_name.endswith('Engine') if args.engine else (class_name in ('UIAction_DisplayMessageBox', 'UTGameUISceneClient', 'UIScene_Confirmation') or
                          'Confirmation' in object_name or object_name.startswith('PCDisplay')) if args.menu else (
                          class_name.startswith('AO2HudItem') or any(term in object_name.lower() for term in
                          ('reload_button', 'action_menu_button', 'minigame_button_', 'coop_call_')))
                if args.contains is not None:
                    wanted = args.contains.lower() in (object_name + ' ' + class_name).lower()
                    if args.include_path and not wanted:
                        wanted = args.contains.lower() in describe(pointer)['path'].lower()
                if args.campaign_network and (class_name in ('AO2GameViewportClient', 'AO2PlayerController') or
                                              class_name.startswith('AO2Character_')):
                    wanted = True
                if args.combat_state and class_name.startswith(('AO2Weap_', 'AO2Char_')):
                    wanted = True
                if not wanted:
                    continue
                vtable = struct.unpack_from('>I', data)[0]
                selected.append({'object': hex(pointer), 'name': object_name, 'path': describe(pointer)['path'],
                    'name_number': number, 'class': class_name, 'vtable': hex(vtable),
                    'slot_110': hex(word(vtable + 0x110)),
                    'words': [hex(x) for x in struct.unpack('>%dI' % ((args.object_bytes or (0x400 if args.engine else 0x200)) // 4),
                                                          read(pointer, args.object_bytes or (0x400 if args.engine else 0x200)))]})
            except ValueError:
                continue
        # Resolve only pointer values that actually occur in the observed object
        # table. Word offsets remain raw observations, not reflected properties.
        for item in selected:
            item['references'] = []
            if args.object_strings:
                item['string_candidates'] = []
                raw = [int(value, 16) for value in item['words']]
                for i in range(0x38 // 4, len(raw) - 2):
                    pointer, size, capacity = raw[i:i+3]
                    if not (0x30000000 <= pointer <= 0x7FFFFFFF and 2 <= size <= capacity <= 1024):
                        continue
                    try:
                        value = read(pointer, size)
                        if value[-1] == 0 and all(32 <= byte < 127 or byte in (9, 10, 13) for byte in value[:-1]):
                            item['string_candidates'].append(dict(offset=hex(i*4), text=value[:-1].decode('ascii')))
                    except ValueError:
                        pass
            if args.sequence_links and item['class'].startswith(('UIAction_', 'SeqAct_', 'SeqCond_', 'SeqEvent_',
                                                                  'AO2SeqAct_', 'AO2SeqCond_', 'AO2SeqEvent_')):
                address = int(item['object'], 16) + 0x94
                header = read(address, 12)
                links, count, capacity = struct.unpack('>III', header)
                if not 0 <= count <= capacity <= 64:
                    raise RuntimeError('Sequence output links outside conservative bounds')
                outputs = read(links, count * 0x34) if count else b''
                item['sequence_outputs'] = []
                for i in range(count):
                    offset = i * 0x34
                    connections, n, maximum, label, size, label_capacity, flags = struct.unpack_from('>7I', outputs, offset)
                    if not 0 <= n <= maximum <= 128 or not 0 <= size <= label_capacity <= 1024:
                        raise RuntimeError('Sequence output data outside conservative bounds')
                    label_text = read(label, size).rstrip(b'\0').decode(errors='replace') if size else ''
                    edges = read(connections, n * 8) if n else b''
                    targets = []
                    for j in range(n):
                        target, input_index = struct.unpack_from('>II', edges, j * 8)
                        targets.append(dict(input_index=input_index, **describe(target)) if target in pointer_set else
                                       dict(object=hex(target), input_index=input_index))
                    item['sequence_outputs'].append(dict(index=i, label=label_text, flags=hex(flags), targets=targets))
                item['sequence_outputs_unchanged'] = header == read(address, 12) and (
                    not count or outputs == read(links, count * 0x34))
            if item['class'] == 'AO2ProfileSettings' and item['vtable'] == '0x8203c660':
                # Verified retail GetProfileSettingValueInt (825A1B50): +4C
                # is a 24-byte value array, +70 a 28-byte metadata array.
                address = int(item['object'], 16)
                header = read(address + 0x4C, 12)
                pointer, count, capacity = struct.unpack('>III', header)
                meta_header = read(address + 0x70, 12)
                meta_pointer, meta_count, meta_capacity = struct.unpack('>III', meta_header)
                if count <= capacity <= 1024 and meta_count <= meta_capacity <= 1024:
                    values = read(pointer, count * 24) if count else b''
                    metadata = read(meta_pointer, meta_count * 28) if meta_count else b''
                    integer_ids = {struct.unpack_from('>I', metadata, i * 28)[0]
                                   for i in range(meta_count) if metadata[i * 28 + 12] == 1}
                    item['profile_integers'] = []
                    for i in range(count):
                        offset = i * 24
                        setting = struct.unpack_from('>I', values, offset + 4)[0]
                        if setting in integer_ids:
                            value = struct.unpack_from('>i', values, offset + 12)[0] if values[offset + 8] == 1 else 0
                            item['profile_integers'].append({'id': setting, 'value': value})
                    if (header != read(address + 0x4C, 12) or meta_header != read(address + 0x70, 12)
                            or (count and values != read(pointer, count * 24))
                            or (meta_count and metadata != read(meta_pointer, meta_count * 28))):
                        raise RuntimeError('Profile settings changed during observation')
            if args.vtable:
                item['vtable_slots'] = [hex(value) for value in struct.unpack(
                    '>128I', read(int(item['vtable'], 16), 512))]
            if args.function_script and item['class'] == 'Function':
                address = int(item['object'], 16)
                header = read(address + 0x54, 12)
                script, size, capacity = struct.unpack('>III', header)
                if not 0 <= size <= capacity <= 256 * 1024 or (size and not script):
                    raise RuntimeError('Function bytecode outside conservative bounds')
                code = read(script, size) if size else b''
                references = []
                for offset in range(max(0, size - 3)):
                    target = struct.unpack_from('>I', code, offset)[0]
                    if target and target in pointer_set:
                        references.append(dict(offset=offset, **describe(target)))
                name_candidates = []
                for offset in range(max(0, size - 9)):
                    # Retail VirtualFunction has a flag byte before FName.
                    # A byte-pattern candidate is not a full VM disassembly.
                    if code[offset:offset+2] != b'\x1b\x00':
                        continue
                    index, number = struct.unpack_from('>II', code, offset + 2)
                    if index < name_count and number < 64:
                        name_candidates.append(dict(offset=offset, opcode=hex(code[offset]),
                                                    name=name(index), name_number=number))
                if header != read(address + 0x54, 12) or (size and code != read(script, size)):
                    raise RuntimeError('Function changed during observation')
                item['script'] = dict(address=hex(script), size=size, bytes=code.hex(),
                                      native=hex(word(address + 0xA8)), references=references,
                                      name_candidates=name_candidates,
                                      note='Pointer matches in raw bytecode are candidates, not decoded instructions.')
            if item['class'] == 'UTGameUISceneClient' and item['vtable'] == '0x820fa198':
                # Verified by the native menu hook; a live observation, not an
                # atomic snapshot. Recheck the header and entries after reading.
                address = int(item['object'], 16) + 0xC0
                header = read(address, 12)
                scenes, count, capacity = struct.unpack('>III', header)
                if count <= capacity <= 64 and (not count or scenes):
                    entries = read(scenes, count * 4) if count else b''
                    active = []
                    for pointer in struct.unpack('>' + 'I' * count, entries):
                        if pointer not in pointer_set or not pointer:
                            raise RuntimeError('Active scene is absent from observed object table')
                        scene = describe(pointer)
                        tag, number = struct.unpack('>II', read(pointer + 0x154, 8))
                        scene.update(scene_tag=name(tag), scene_tag_number=number)
                        active.append(scene)
                    unchanged = header == read(address, 12)
                    unchanged = unchanged and (not count or entries == read(scenes, count * 4))
                    item['active_scenes'] = active
                    item['active_scene_list_unchanged'] = unchanged
                else:
                    item['active_scene_error'] = 'Array outside conservative bounds'
            if item['class'] == 'Texture2D' and item['vtable'] == '0x82081df0':
                # Retail UTexture2D streaming update 82585668 passes +B8 to
                # 82580F48. This is a resource observation, not a GPU binding.
                resource = int(item['words'][0xB8 // 4], 16)
                if resource:
                    try:
                        resource_words = struct.unpack('>32I', read(resource, 0x80))
                        item['texture_resource'] = {'object': hex(resource),
                            'words': [hex(x) for x in resource_words]}
                        if 0x82000000 <= resource_words[0] < 0x83000000:
                            item['texture_resource']['vtable_words'] = [hex(x) for x in
                                struct.unpack('>16I', read(resource_words[0], 0x40))]
                    except ValueError as error:
                        item['texture_resource_error'] = str(error)
            for offset, raw in enumerate(item['words']):
                pointer = int(raw, 16)
                if offset < 0x38 // 4 or pointer not in pointer_set or not pointer:
                    continue
                try:
                    target = describe(pointer)
                    target['offset'] = hex(offset * 4)
                    item['references'].append(target)
                except ValueError:
                    continue
        report = {'pid': locator['pid'], 'executable_sha256': hashlib.sha256(expected.read_bytes()).hexdigest(),
                  'selection': ('contains:'+args.contains) if args.contains else 'engine' if args.engine else 'menu' if args.menu else 'hud',
                  'virtual_base': hex(base), 'object_count': object_count, 'name_count': name_count,
                  'requested_bytes': read_bytes, 'failed_reads': failures,
                  'elapsed_seconds': time.monotonic() - started, 'objects': selected,
                  'limitation': __doc__.strip()}
        if args.campaign_network:
            read_phase = 'campaign_network'
            # Retail 82955C38/8294CD00/82961608 and 82218038 refer to these
            # globals. Observe only; do not invoke service virtual functions.
            native = {}
            def native_object(label, pointer, size):
                if not pointer:
                    native[label] = None
                    return
                raw = read(pointer, size)
                table = struct.unpack_from('>I', raw)[0]
                item = {'address': hex(pointer), 'words': [hex(x) for x in struct.unpack(f'>{size//4}I', raw)]}
                if 0x82000000 <= table < 0x82200000:
                    item['vtable'] = hex(table)
                    item['slots'] = [hex(x) for x in struct.unpack('>64I', read(table, 256))]
                native[label] = item
            backend = word(0x831228E4)
            root_service = word(0x8314C964)
            native_object('plasma_backend', backend, 0xD58)
            native_object('network_mode', word(0x831228D0), 0x54)
            simulation = word(0x831228C8)
            native_object('simulation', simulation, 0x13C)
            native_object('replication_queue', word(0x831228E0), 0x314)
            checkpoint, checkpoint_size, checkpoint_capacity = struct.unpack('>III', read(0x830AB2E4, 12))
            native['checkpoint'] = {'address': hex(checkpoint), 'size': checkpoint_size,
                                    'capacity': checkpoint_capacity}
            if 0 < checkpoint_size <= checkpoint_capacity <= 4*1024*1024:
                native['checkpoint']['sha256'] = hashlib.sha256(read(checkpoint, checkpoint_size)).hexdigest()
            if backend:
                players = word(backend+0xB80)
                native_object('native_players', players, 0x18)
                if players:
                    for index in range(4):
                        native_object('native_player_'+str(index), word(players+8+index*4), 0x58)
            if simulation:
                for index in range(4):
                    native_object('local_simulation_'+str(index), word(simulation+index*4), 0x4C4)
                for index in range(3):
                    native_object('remote_simulation_'+str(index), word(simulation+0x14+index*4), 0x4D0)
            native_object('service_root', root_service, 0xE8)
            if root_service:
                manager = word(root_service+0xD4)
                native_object('service_manager', manager, 0x100)
                if manager:
                    for slot, label in [(0x40, 'service_game'), (0x44, 'service_session')]:
                        method = word(word(manager)+slot)
                        code = read(method, 8)
                        instruction, end = struct.unpack('>II', code)
                        # Only follow a verified lwz r3,offset(r3); blr getter.
                        if instruction & 0xFFFF0000 == 0x80630000 and end == 0x4E800020:
                            offset = struct.unpack('>h', code[2:4])[0]
                            native_object(label, word(manager+offset), 0x100)
                        else:
                            native[label] = {'getter': hex(method), 'code': code.hex(), 'not_followed': True}
            native['event_names'] = {hex(a): name(word(a)) for a in [0x83120AE4, 0x831206BC, 0x83120014, 0x83120310]}
            native['viewport_event_names'] = {hex(a): name(word(a)) for a in
                [0x8311FB90, 0x8311FE54, 0x83120048, 0x83120274, 0x831202E4, 0x83120BB4]}
            native['viewport_topology'] = []
            # Engine.Actor.Location's retail StructProperty reports 12 bytes
            # at +E4. Keep actor identity and raw words for audit; sampling two
            # processes is not atomic and must be compared after input settles.
            native['character_positions'] = []
            if args.combat_state:
                read_phase = 'combat_state'
                if not simulation:
                    raise RuntimeError('No native simulation for combat sampling')
                def combat_frame_stamp():
                    produced, available, consumed = struct.unpack('>iii', read(simulation+0x5C,12))
                    return dict(produced=produced, available=available, consumed=consumed,
                                monotonic_seconds=time.monotonic())
                native['combat_sampling_window'] = {'begin':combat_frame_stamp()}
                missing = combat_specs.keys() - combat_properties.keys()
                if missing:
                    raise RuntimeError('Missing combat reflection: '+', '.join(sorted(missing)))
                native['combat_properties'] = combat_properties
                native['combat_state'] = []
                ancestry = {}
                def class_ancestry(cls):
                    if cls not in ancestry:
                        paths, seen = set(), set()
                        current = cls
                        while current:
                            if current not in pointer_set or current in seen or len(seen)>=32:
                                raise RuntimeError('Invalid combat class ancestry')
                            seen.add(current)
                            desc = describe(current)
                            if desc['class'] != 'Class':
                                raise RuntimeError('Combat superclass is not a Class')
                            paths.add(desc['path'])
                            current = word(current+0x3C)
                        ancestry[cls] = paths
                    return ancestry[cls]
                for item in selected:
                    if '.TheWorld.' not in item['path'] or not item['class'].startswith(('AO2Character_', 'AO2Char_', 'AO2Weap_')):
                        continue
                    actor = int(item['object'],16)
                    parents = class_ancestry(word(actor+0x34))
                    if not parents.intersection(('Engine.Pawn', 'UTGame.UTWeapon')):
                        continue
                    values = {}
                    for path, prop in combat_properties.items():
                        if path.rsplit('.',1)[0] not in parents:
                            continue
                        value = word(actor+prop['offset'])
                        if prop['type'] == 'ObjectProperty':
                            values[path] = describe(value) if value in pointer_set and value else None
                        else:
                            values[path] = struct.unpack('>i',struct.pack('>I',value))[0]
                    native['combat_state'].append({k:item[k] for k in ('object','path','name_number','class')} | {'values':values})
                native['combat_sampling_window']['end'] = combat_frame_stamp()
                read_phase = 'campaign_network'
            # Verified against the retail IntProperty: one 4-byte value at +BBC.
            # This is the partner's checkpoint cash, not the profile wallet.
            native['controller_checkpoint_cash'] = [
                {'object': item['object'], 'path': item['path'],
                 'remote_total': word(int(item['object'], 16)+0xBBC)}
                for item in selected if item['class']=='AO2PlayerController' and
                not item['name'].startswith('Default__')]
            for item in selected:
                if not item['class'].startswith('AO2Character_') or '.TheWorld.' not in item['path']:
                    continue
                actor = int(item['object'], 16)
                native['character_positions'].append({'object': item['object'], 'path': item['path'],
                    'name_number': item['name_number'], 'class': item['class'],
                    'location': list(struct.unpack('>fff', read(actor+0xE4, 12)))})
            for item in selected:
                if item['class'] != 'AO2GameViewportClient' or not item['path'].startswith('Transient.'):
                    continue
                viewport = int(item['object'], 16)
                def viewport_info(address):
                    ids, count, capacity = struct.unpack('>III', read(address, 12))
                    if count > 4 or capacity < count or capacity > 1024:
                        raise ValueError('Invalid native viewport controller array')
                    return {'controller_ids': list(struct.unpack(f'>{count}I', read(ids, count*4))) if count else [],
                            'size': list(struct.unpack('>ff', read(address+12, 8)))}
                remotes, count, capacity = struct.unpack('>III', read(viewport+0x178, 12))
                if count > 4 or capacity < count or capacity > 1024:
                    raise ValueError('Invalid native remote viewport array')
                native['viewport_topology'].append({'object': item['object'],
                    'local': viewport_info(viewport+0x15C),
                    'remote': [viewport_info(remotes+i*0x1C) for i in range(count)]})
            # OnGameStart and AddRemoteWeapon/Armor populate these retail
            # arrays. Observe the game-owned result independently of PC logs.
            native['equipment'] = []
            for item in selected:
                if item['vtable'] != '0x820e90c8' or not item['path'].startswith('Transient.'):
                    continue
                plasma = int(item['object'], 16)
                equipment = {'object': item['object'], 'weapons': [], 'armor': []}
                def equipment_string(address):
                    pointer, count, capacity = struct.unpack('>III', read(address, 12))
                    if not 1 <= count <= 129 or capacity < count:
                        raise ValueError('Invalid equipment FString bounds')
                    text = read(pointer, count)
                    if text[-1] != 0:
                        raise ValueError('Equipment FString lacks terminator')
                    return text[:-1].decode('ascii')
                for offset, stride, limit, label in [(0x194, 56, 160, 'weapons'), (0x1A0, 12, 4, 'armor')]:
                    pointer, count, capacity = struct.unpack('>III', read(plasma+offset, 12))
                    if count > limit or capacity < count:
                        raise ValueError('Invalid equipment array bounds')
                    for index in range(count):
                        address = pointer+index*stride
                        record = {'player': word(address)}
                        if label == 'weapons':
                            record.update(archetype=equipment_string(address+4),
                                          class_name=equipment_string(address+0x10),
                                          upgrades=list(struct.unpack('>7I', read(address+0x1C, 28))))
                        else:
                            record.update(armor=word(address+4), mask=word(address+8))
                        equipment[label].append(record)
                native['equipment'].append(equipment)
            report['campaign_network'] = native
            report['requested_bytes'] = read_bytes
            report['elapsed_seconds'] = time.monotonic()-started
        if args.engine:
            read_phase = 'engine_settings'
            # Retail engine Exec 82638F30-82638F40 passes this native settings
            # object to 82584E58. Store raw observations, not reflected fields.
            if read(0x82638F30, 20) != bytes.fromhex('3d60830b808105bc7f65db78386be5044bf4bf19'):
                raise RuntimeError('Unexpected retail system-settings dispatch')
            settings_address = 0x830AE504
            report['native_system_settings'] = {
                'address': hex(settings_address),
                'words': [hex(x) for x in struct.unpack('>104I', read(settings_address, 0x1A0))]}
            report['requested_bytes'] = read_bytes
            report['elapsed_seconds'] = time.monotonic() - started
        # Include optional observations in the final totals too. Never conceal
        # failed reads just because they occurred after the initial inventory.
        report.update(requested_bytes=read_bytes, failed_reads=failures,
                      failed_read_ranges=failed_ranges, failed_read_ranges_truncated=failures>len(failed_ranges),
                      elapsed_seconds=time.monotonic()-started)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        print(f'Observed {len(selected)} matching objects; {failures} failed reads')
    finally:
        close(handle)


if __name__ == '__main__':
    main()
