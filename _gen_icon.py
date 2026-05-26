import struct, zlib, pathlib

def create_png(size, bg_color, check_color):
    width = height = size
    pixels = []
    center = size / 2
    for y in range(height):
        row = []
        for x in range(width):
            cx, cy = abs(x - center), abs(y - center)
            corner_r = size * 0.22
            inside = True
            if cx > size/2 - corner_r and cy > size/2 - corner_r:
                dx = cx - (size/2 - corner_r)
                dy = cy - (size/2 - corner_r)
                if dx*dx + dy*dy > corner_r*corner_r:
                    inside = False
            elif x < size*0.08 or x >= size*0.92 or y < size*0.08 or y >= size*0.92:
                inside = False
            is_check = False
            thickness = size * 0.055
            if 0.26 < x/size < 0.44:
                t1 = (x/size - 0.28) / (0.42 - 0.28)
                expected_y = 0.5 + t1 * (0.64 - 0.5)
                if abs(y/size - expected_y) < thickness/size:
                    is_check = True
            if 0.40 < x/size < 0.74:
                t2 = (x/size - 0.42) / (0.72 - 0.42)
                expected_y = 0.64 + t2 * (0.30 - 0.64)
                if abs(y/size - expected_y) < thickness/size:
                    is_check = True
            if is_check and inside:
                row.extend(check_color + [255])
            elif inside:
                row.extend(bg_color + [255])
            else:
                row.extend([0, 0, 0, 0])
        pixels.append(bytes([0] + row))
    raw = b''.join(pixels)
    def make_chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    png = b'\x89PNG\r\n\x1a\n'
    png += make_chunk(b'IHDR', ihdr)
    png += make_chunk(b'IDAT', zlib.compress(raw))
    png += make_chunk(b'IEND', b'')
    return png

bg = (26, 115, 232)
check = (255, 255, 255)
out = pathlib.Path(r'D:\Project\MCChecker\app\static\favicon.png')
out.write_bytes(create_png(64, bg, check))
print('Created favicon.png')