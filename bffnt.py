#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import math
import os.path
import struct
import sys
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, assert_never
from dataclasses import dataclass

import png
import typer
from typer import Option as Opt

# FINF = Font Info
# TGLP = Texture Glyph
# CWDH = Character Widths
# CMAP = Character Mapping

VERSIONS = (0x04000000, 0x03000000)

FFNT_HEADER_SIZE = 0x14
FINF_HEADER_SIZE = 0x20
TGLP_HEADER_SIZE = 0x20
CWDH_HEADER_SIZE = 0x10
CMAP_HEADER_SIZE = 0x14

FFNT_HEADER_MAGIC = (b'FFNT', b'FFNU')
FINF_HEADER_MAGIC = b'FINF'
TGLP_HEADER_MAGIC = b'TGLP'
CWDH_HEADER_MAGIC = b'CWDH'
CMAP_HEADER_MAGIC = b'CMAP'

FFNT_HEADER_STRUCT = '%s4s2H3I'
FINF_HEADER_STRUCT = '%s4sI4B2H4B3I'
TGLP_HEADER_STRUCT = '%s4sI4BI6HI'
CWDH_HEADER_STRUCT = '%s4sI2HI'
CMAP_HEADER_STRUCT = '%s4sI4HI'

class Format(Enum):
    RGBA8 = 0x00
    RGB8 = 0x01
    RGBA5551 = 0x02
    RGB565 = 0x03
    RGBA4 = 0x04
    LA8 = 0x05
    HILO8 = 0x06
    L8 = 0x07
    A8 = 0x08
    LA4 = 0x09
    L4 = 0x0A
    A4 = 0x0B
    #TODO: Fix this, in fact, it should be BC4
    ETC1 = 0x0C
    ETC1A4 = 0x0D

    def format_size(self) -> int:
        match self:
            case Format.RGBA8: return 32
            case Format.RGB8: return 24
            case Format.RGBA5551: return 16
            case Format.RGB565: return 16
            case Format.RGBA4: return 16
            case Format.LA8: return 16
            case Format.HILO8: return 16
            case Format.L8: return 8
            case Format.A8: return 8
            case Format.LA4: return 8
            case Format.L4: return 4
            case Format.A4: return 4
            case Format.ETC1: return 64
            case Format.ETC1A4: return 128

ETC_INDIV_RED1_OFFSET = 60
ETC_INDIV_GREEN1_OFFSET = 52
ETC_INDIV_BLUE1_OFFSET = 44

ETC_DIFF_RED1_OFFSET = 59
ETC_DIFF_GREEN1_OFFSET = 51
ETC_DIFF_BLUE_OFFSET = 43

ETC_RED2_OFFSET = 56
ETC_GREEN2_OFFSET = 48
ETC_BLUE2_OFFSET = 40

ETC_TABLE1_OFFSET = 37
ETC_TABLE2_OFFSET = 34

ETC_DIFFERENTIAL_BIT = 33
ETC_ORIENTATION_BIT = 32

ETC_MODIFIERS = [
    [2, 8],
    [5, 17],
    [9, 29],
    [13, 42],
    [18, 60],
    [24, 80],
    [33, 106],
    [47, 183]
]

MAPPING_DIRECT = 0x00
MAPPING_TABLE = 0x01
MAPPING_SCAN = 0x02

MAPPING_METHODS = {
    MAPPING_DIRECT: 'Direct',
    MAPPING_TABLE: 'Table',
    MAPPING_SCAN: 'Scan'
}

TGLP_DATA_OFFSET = 0x2000

@dataclass
class CwdhSectionData:
    left: Any
    glyph: Any
    char: Any

@dataclass
class CwdhSection:
    start: int
    end: int
    size: int
    data: List[CwdhSectionData]

class Bffnt:
    order = None
    invalid = False
    file_size = 0
    filename = ''
    font_info = {}
    tglp_offset: int
    cwdh_offset: int
    cmap_offset: int

    tglp = {}
    cwdh_sections: List[CwdhSection] = []
    cmap_sections = []

    def __init__(self, verbose=False, debug=False, load_order='<'):
        self.verbose = verbose
        self.debug = debug
        self.load_order = load_order

    def read(self, filename: str):
        data = open(filename, 'rb').read()
        self.file_size = len(data)
        self.filename = filename

        self._parse_header(data[:FFNT_HEADER_SIZE])
        position = FFNT_HEADER_SIZE
        if self.invalid:
            return

        self._parse_finf(data[position:position + FINF_HEADER_SIZE])
        if self.invalid:
            return

        # navigate to TGLP (offset skips the MAGIC+size)
        position = self.tglp_offset - 8
        self._parse_tglp_header(data[position:position + TGLP_HEADER_SIZE])
        if self.invalid:
            return

        # navigate to CWDH (offset skips the MAGIC+size)
        cwdh = self.cwdh_offset
        while cwdh > 0:
            position = cwdh - 8
            cwdh = self._parse_cwdh_header(data[position:position + CWDH_HEADER_SIZE])
            if cwdh is None:
                self.invalid = True
                return

            position += CWDH_HEADER_SIZE
            info = self.cwdh_sections[-1]
            self._parse_cwdh_data(info, data[position:position + info.size - CWDH_HEADER_SIZE])

        # navigate to CMAP (offset skips the MAGIC+size)
        cmap = self.cmap_offset
        while cmap > 0:
            position = cmap - 8
            cmap = self._parse_cmap_header(data[position:position + CMAP_HEADER_SIZE])
            if self.invalid:
                return

            position += CMAP_HEADER_SIZE
            info = self.cmap_sections[-1]
            self._parse_cmap_data(info, data[position:position + info['size'] - CMAP_HEADER_SIZE])

        # convert pixels to RGBA8
        self._parse_tglp_data(data)

    def load(self, json_filename: str):
        json_data = json.load(open(json_filename, 'r', encoding="utf-8"))

        self.order = self.load_order
        self.version = json_data['version']
        self.filetype = json_data['fileType']

        self.font_info = json_data['fontInfo']
        tex_info = json_data['textureInfo']

        try:
            sheet_pixel_format = Format[tex_info['sheetInfo']['colorFormat']]
        except KeyError:
            print('Invalid pixel format: %s' % tex_info['sheetInfo']['colorFormat'])
            self.invalid = True
            return

        self.tglp = {
            'glyph': {
                'width': tex_info['glyph']['width'],
                'height': tex_info['glyph']['height'],
                'baseline': tex_info['glyph']['baseline']
            },
            'sheetCount': tex_info['sheetCount'],
            'sheet': {
                'cols': tex_info['sheetInfo']['cols'],
                'rows': tex_info['sheetInfo']['rows'],
                'width': tex_info['sheetInfo']['width'],
                'height': tex_info['sheetInfo']['height'],
                'format': sheet_pixel_format
            }
        }

        widths = json_data['glyphWidths']
        cwdh = CwdhSection(start=0, end=0, size=0, data=[])

        widest = 0
        glyph_indicies = list(widths.keys())
        glyph_indicies.sort(key=self._int_sort)

        cwdh.end = int(glyph_indicies[-1], base=10)

        for idx in glyph_indicies:
            cwdh_data = CwdhSectionData(**widths[idx])

            cwdh.data.append(cwdh_data)
            if cwdh_data.char > widest:
                widest = cwdh_data.char

        self.tglp['maxCharWidth'] = widest

        self.cwdh_sections = [cwdh]

        glyph_map = json_data['glyphMap']
        glyph_ords = list(glyph_map.keys())
        glyph_ords.sort()
        cmap = {
            'start': ord(glyph_ords[0]),
            'end': ord(glyph_ords[-1]),
            'type': MAPPING_SCAN,
            'entries': {}
        }

        for entry in range(cmap['start'], cmap['end'] + 1):
            utf16 = chr(entry)
            if utf16 in glyph_map:
                cmap['entries'][utf16] = glyph_map[utf16]

        self.cmap_sections = [cmap]

    def _int_sort(self, n):
        return int(n, 10)

    def extract(self, ensure_ascii=True):
        if self.verbose:
            print('Extracting...')
        basename_ = os.path.splitext(os.path.basename(self.filename))[0]

        glyph_widths: Dict[int, Dict[str, int]] = {}
        for cwdh in self.cwdh_sections:
            for index in range(cwdh.start, cwdh.end + 1):
                data = cwdh.data[index - cwdh.start]
                glyph_widths[index] = {
                    'char': data.char,
                    'glyph': data.glyph,
                    'left': data.left,
                }

        glyph_mapping = {}
        for cmap in self.cmap_sections:
            if cmap['type'] == MAPPING_DIRECT:
                for code in range(cmap['start'], cmap['end'] + 1):
                    glyph_mapping[chr(code)] = code - cmap['start'] + cmap['indexOffset']
            elif cmap['type'] == MAPPING_TABLE:
                for code in range(cmap['start'], cmap['end'] + 1):
                    index = cmap['indexTable'][code - cmap['start']]
                    if index != 0xFFFF:
                        glyph_mapping[chr(code)] = index
            elif cmap['type'] == MAPPING_SCAN:
                for code in cmap['entries'].keys():
                    glyph_mapping[code] = cmap['entries'][code]

        # save JSON manifest
        json_file_ = open('%s_manifest.json' % basename_, 'w', encoding="utf-8")
        json_file_.write(json.dumps({
            'version': self.version,
            'fileType': self.filetype,
            'fontInfo': self.font_info,
            'textureInfo': {
                'glyph': self.tglp['glyph'],
                'sheetCount': self.tglp['sheetCount'],
                'sheetInfo': {
                    'cols': self.tglp['sheet']['cols'],
                    'rows': self.tglp['sheet']['rows'],
                    'width': self.tglp['sheet']['width'],
                    'height': self.tglp['sheet']['height'],
                    'colorFormat': self.tglp['sheet']['format'].name
                }
            },
            'glyphWidths': glyph_widths,
            'glyphMap': glyph_mapping
        }, indent=2, sort_keys=True, ensure_ascii=ensure_ascii))
        json_file_.close()

        # save sheet bitmaps
        for i in range(self.tglp['sheetCount']):
            sheet = self.tglp['sheets'][i]
            width = sheet['width']
            height = sheet['height']
            png_data = []
            for y in range(height):
                row = []
                for x in range(width):
                    for color in sheet['data'][x + (y * width)]:
                        row.append(color)

                png_data.append(row)

            png.from_array(png_data, 'RGBA').save('%s_sheet%d.png' % (basename_, i))
        print('Done')

    def save(self, filename):
        if self.verbose:
            print('Packing...')
        file_ = open(filename, 'wb')
        basename_ = os.path.splitext(os.path.basename(filename))[0]
        section_count = 0

        bom = 0
        if self.order == '>':
            bom = 0xFFFE
        elif self.order == '<':
            bom = 0xFEFF

        # write header
        file_size_pos = 0x0C
        section_count_pos = 0x10
        magic = self.filetype.upper().encode('ascii')

        data = struct.pack(FFNT_HEADER_STRUCT % self.order, magic, bom, FFNT_HEADER_SIZE, self.version, 0, 0)
        file_.write(data)

        position = FFNT_HEADER_SIZE

        # write finf
        if self.verbose:
            print('Writing FINF...')
        font_info = self.font_info
        default_width = font_info['defaultWidth']
        finf_tglp_offset_pos = position + 0x14
        finf_cwdh_offset_pos = position + 0x18
        finf_cmap_offset_pos = position + 0x1C
        data = struct.pack(FINF_HEADER_STRUCT % self.order, FINF_HEADER_MAGIC, FINF_HEADER_SIZE, font_info['fontType'],
                           font_info['height'], font_info['width'], font_info['ascent'], font_info['lineFeed'],
                           font_info['alterCharIdx'], default_width['left'], default_width['glyphWidth'],
                           default_width['charWidth'], font_info['encoding'], 0, 0, 0)
        file_.write(data)
        position += FINF_HEADER_SIZE

        section_count += 1

        # write tglp
        if self.verbose:
            print('Writing TGLP...')
        tglp = self.tglp
        sheet = tglp['sheet']
        tglp_size_pos = position + 0x04
        tglp_data_size = int(sheet['width'] * sheet['height'] * (sheet['format'].format_size() / 8.0))

        file_.seek(finf_tglp_offset_pos)
        file_.write(struct.pack('%sI' % self.order, position + 8))
        file_.seek(position)

        tglp_start_pos = position
        data = struct.pack(TGLP_HEADER_STRUCT % self.order, TGLP_HEADER_MAGIC, 0, tglp['glyph']['width'],
                       tglp['glyph']['height'], tglp['sheetCount'], tglp['maxCharWidth'], tglp_data_size,
                       tglp['glyph']['baseline'], sheet['format'].value, sheet['cols'], sheet['rows'], sheet['width'],
                       sheet['height'], TGLP_DATA_OFFSET)
        file_.write(data)

        file_.seek(TGLP_DATA_OFFSET)
        position = TGLP_DATA_OFFSET

        section_count += 1

        for idx in range(tglp['sheetCount']):
            sheet_filename = '%s_sheet%d.png' % (basename_, idx)
            sheet_file_ = open(sheet_filename, 'rb')

            reader = png.Reader(file=sheet_file_)
            width, height, pixels, metadata = reader.read()

            if width != sheet['width'] or height != sheet['height']:
                print('Invalid sheet PNG:\nexpected an image size of %dx%d but %s is %dx%d' %
                      (sheet['width'], sheet['height'], sheet_filename, width, height))
                self.invalid = True
                return

            if metadata['bitdepth'] != 8 or metadata['alpha'] != True:
                print('Invalid sheet PNG:\nexpected a PNG8 with alpha')

            def is_power_of_two(n):
                return n != 0 and (n & (n - 1) == 0)

            # From https://stackoverflow.com/a/14267825
            def next_power_of_two(n):
                return 1 if n == 0 else 1 << (n - 1).bit_length()

            if not is_power_of_two(width) or not is_power_of_two(height):
                print(f'Note: Non power of 2 image dimensions ({width}x{height}) might produce a broken bffnt!')
                sug_size_1 = next_power_of_two(math.ceil(math.sqrt(width * height)))
                sug_size_2 = next_power_of_two(math.ceil(width * height / sug_size_1))
                # ignore edge case like glyph width being smaller than suggested width
                suggest_width = min(sug_size_1, sug_size_2)

                # why is glyph width/height one less then the actual pixel width?
                suggest_cols = math.floor(suggest_width / (tglp['glyph']['width'] + 1))
                suggest_height = next_power_of_two(math.ceil(
                    sheet['cols'] * sheet['rows'] / suggest_cols * (tglp['glyph']['height'] + 1)
                ))
                suggest_rows = math.floor(suggest_height / (tglp['glyph']['height'] + 1))

                print(f'      If you do experience issues try a {suggest_width}x{suggest_height} image with {suggest_cols} cols and {suggest_rows} rows')

                # should this be a hard error?
                # self.invalid = True
                # return

            self.tglp['sheet']['size'] = tglp_data_size

            bmp: List[Tuple[int, int, int, int]] = []
            for row in list(pixels):
                for pixel in range(0, len(row), 4):
                    bmp.append((
                        row[pixel],
                        row[pixel + 1],
                        row[pixel + 2],
                        row[pixel + 3],
                    ))

            data = self._bitmap_to_sheet(bmp)

            data = struct.pack('%dB' % len(data), *data)
            file_.write(data)
            position += len(data)

            sheet_file_.close()

        file_.seek(tglp_size_pos)
        file_.write(struct.pack('%sI' % self.order, position - tglp_start_pos))

        file_.seek(finf_cwdh_offset_pos)
        file_.write(struct.pack('%sI' % self.order, position + 8))
        file_.seek(position)

        # write cwdh
        if self.verbose:
            print('Writing CWDH...')
        prev_cwdh_offset_pos = 0
        for cwdh in self.cwdh_sections:
            section_count += 1
            if prev_cwdh_offset_pos > 0:
                file_.seek(prev_cwdh_offset_pos)
                file_.write(struct.pack('%sI' % self.order, position + 8))
                file_.seek(position)

            size_pos = position + 0x04
            prev_cwdh_offset_pos = position + 0x0C

            start_pos = position
            data = struct.pack(CWDH_HEADER_STRUCT % self.order, CWDH_HEADER_MAGIC, 0, cwdh.start, cwdh.end, 0)
            file_.write(data)
            position += CWDH_HEADER_SIZE

            for code in range(cwdh.start, cwdh.end + 1):
                widths = cwdh.data[code]
                file_.write(struct.pack('=bbb', widths.left, widths.glyph, widths.char))
                position += 3

            # TODO: is this always added? or should it be tracked in the manifest
            padding = position % 4
            for _ in range(padding):
                file_.write(struct.pack('b', 0))
            position += padding

            file_.seek(size_pos)
            file_.write(struct.pack('%sI' % self.order, position - start_pos))
            file_.seek(position)

        file_.seek(finf_cmap_offset_pos)
        file_.write(struct.pack('%sI' % self.order, position + 8))
        file_.seek(position)

        # write cmap
        if self.verbose:
            print('Writing CMAP...')
        prev_cmap_offset_pos = 0
        for cmap in self.cmap_sections:
            section_count += 1
            if prev_cmap_offset_pos > 0:
                file_.seek(prev_cmap_offset_pos)
                file_.write(struct.pack('%sI' % self.order, position + 8))
                file_.seek(position)

            size_pos = position + 0x04
            prev_cmap_offset_pos = position + 0x10

            start_pos = position
            data = struct.pack(CMAP_HEADER_STRUCT % self.order, CMAP_HEADER_MAGIC, 0, cmap['start'], cmap['end'],
                               cmap['type'], 0, 0)
            file_.write(data)
            position += CMAP_HEADER_SIZE

            file_.write(struct.pack('%sH' % self.order, len(cmap['entries'])))
            position += 2

            if cmap['type'] == MAPPING_DIRECT:
                file_.write(struct.pack('%sH' % self.order, cmap['indexOffset']))
                position += 2
            elif cmap['type'] == MAPPING_TABLE:
                for index in cmap['indexTable']:
                    file_.write(struct.pack('%sH' % self.order, index))
                    position += 2
            elif cmap['type'] == MAPPING_SCAN:
                keys = list(cmap['entries'].keys())
                keys.sort()
                for code in keys:
                    index = cmap['entries'][code]
                    file_.write(struct.pack('%s2H' % self.order, ord(code), index))
                    position += 4

            # TODO: is this always added? or should it be tracked in the manifest
            padding = position % 4
            for _ in range(padding):
                file_.write(struct.pack('b', 0))
            position += padding

            file_.seek(size_pos)
            file_.write(struct.pack('%sI' % self.order, position - start_pos))
            file_.seek(position)

        # fill in size/offset placeholders
        file_.seek(file_size_pos)
        file_.write(struct.pack('%sI' % self.order, position))

        file_.seek(section_count_pos)
        file_.write(struct.pack('%sI' % self.order, section_count))
        if self.verbose:
            print('Done!')

    def _parse_header(self, data):
        bom = struct.unpack_from('>H', data, 4)[0]
        if bom == 0xFFFE:
            self.order = '<'
        elif bom == 0xFEFF:
            self.order = '>'

        if self.order is None:
            print('Invalid byte-order marker: 0x%x (expected 0xFFFE or 0xFEFF)' % bom)
            self.invalid = True
            return

        magic, bom, header_size, self.version, file_size, sections = struct.unpack(FFNT_HEADER_STRUCT % self.order, data)

        if magic not in FFNT_HEADER_MAGIC:
            print('Invalid FFNT magic bytes: %s (expected %s)' % (magic, FFNT_HEADER_MAGIC))
            self.invalid = True
            return
        self.filetype = magic.decode('ascii').lower()


        if self.version not in VERSIONS:
            print('Unknown version: 0x%08x (expected one of %s)' %
                  (self.version, ', '.join('0x%08x' % k for k in VERSIONS)))
            self.invalid = True
            return

        if header_size != FFNT_HEADER_SIZE:
            print('Invalid header size: %d (expected %d)' % (header_size, FFNT_HEADER_SIZE))
            self.invalid = True
            return

        if file_size != self.file_size:
            print('Invalid file size: %d (expected %d)' % (file_size, self.file_size))
            self.invalid = True
            return

        self.sections = sections

        if self.debug:
            print('FFNT Magic: %s' % magic)
            print('FFNT BOM: %s (0x%x)' % (self.order, bom))
            print('FFNT Header Size: %d' % header_size)
            print('FFNT Version: 0x%08x' % self.version)
            print('FFNT File Size: %d' % file_size)
            print('FFNT Sections: %d\n' % sections)

    def _parse_finf(self, data):
        magic, section_size, font_type, height, width, ascent, line_feed, alter_char_idx, def_left, def_glyph_width, \
                def_char_width, encoding, tglp_offset, cwdh_offset, cmap_offset \
                = struct.unpack(FINF_HEADER_STRUCT % self.order, data)

        if magic != FINF_HEADER_MAGIC:
            print('Invalid FINF magic bytes: %s (expected %s)' % (magic, FINF_HEADER_MAGIC))
            self.invalid = True
            return

        if section_size != FINF_HEADER_SIZE:
            print('Invalid FINF size: %d (expected %d)' % (section_size, FINF_HEADER_SIZE))
            self.invalid = True
            return

        self.font_info = {
            'height': height,
            'width': width,
            'ascent': ascent,
            'lineFeed': line_feed,
            'alterCharIdx': alter_char_idx,
            'defaultWidth': {
                'left': def_left,
                'glyphWidth': def_glyph_width,
                'charWidth': def_char_width
            },
            'fontType': font_type,
            'encoding': encoding
        }

        self.tglp_offset = tglp_offset
        self.cwdh_offset = cwdh_offset
        self.cmap_offset = cmap_offset

        if self.debug:
            print('FINF Magic: %s' % magic)
            print('FINF Section Size: %d' % section_size)
            print('FINF Font Type: 0x%x' % font_type)
            print('FINF Height: %d' % height)
            print('FINF Width: %d' % width)
            print('FINF Ascent: %d' % ascent)
            print('FINF Line feed: %d' % line_feed)
            print('FINF Alter Character Index: %d' % alter_char_idx)
            print('FINF Default Width, Left: %d' % def_left)
            print('FINF Default Glyph Width: %d' % def_glyph_width)
            print('FINF Default Character Width: %d' % def_char_width)
            print('FINF Encoding: %d' % encoding)
            print('FINF TGLP Offset: 0x%08x' % tglp_offset)
            print('FINF CWDH Offset: 0x%08x' % cwdh_offset)
            print('FINF CMAP Offset: 0x%08x\n' % cmap_offset)

    def _parse_tglp_header(self, data):
        magic, section_size, cell_width, cell_height, num_sheets, max_char_width, sheet_size, baseline_position, \
                sheet_pixel_format, num_sheet_cols, num_sheet_rows, sheet_width, sheet_height, sheet_data_offset \
                = struct.unpack(TGLP_HEADER_STRUCT % self.order, data)
        sheet_pixel_format = Format(sheet_pixel_format)

        if magic != TGLP_HEADER_MAGIC:
            print('Invalid TGLP magic bytes: %s (expected %s)' % (magic, TGLP_HEADER_MAGIC))
            self.invalid = True
            return

        self.tglp = {
            'size': section_size,
            'glyph': {
                'width': cell_width,
                'height': cell_height,
                'baseline': baseline_position
            },
            'sheetCount': num_sheets,
            'sheet': {
                'size': sheet_size,
                'cols': num_sheet_cols,
                'rows': num_sheet_rows,
                'width': sheet_width,
                'height': sheet_height,
                'format': sheet_pixel_format
            },
            'sheetOffset': sheet_data_offset
        }

        if self.debug:
            print('TGLP Magic: %s' % magic)
            print('TGLP Section Size: %d' % section_size)
            print('TGLP Cell Width: %d' % cell_width)
            print('TGLP Cell Height: %d' % cell_height)
            print('TGLP Sheet Count: %d' % num_sheets)
            print('TGLP Max Character Width: %d' % max_char_width)
            print('TGLP Sheet Size: %d' % sheet_size)
            print('TGLP Baseline Position: %d' % baseline_position)
            print('TGLP Sheet Image Format: 0x%x (%s)' % (sheet_pixel_format.value, sheet_pixel_format.name))
            print('TGLP Sheet Rows: %d' % num_sheet_rows)
            print('TGLP Sheet Columns: %d' % num_sheet_cols)
            print('TGLP Sheet Width: %d' % sheet_width)
            print('TGLP Sheet Height: %d' % sheet_height)
            print('TGLP Sheet Data Offset: 0x%08x\n' % sheet_data_offset)

    def _parse_tglp_data(self, data):
        position = self.tglp['sheetOffset']
        self.tglp['sheets'] = []
        format_: Format = self.tglp['sheet']['format']
        for _ in range(self.tglp['sheetCount']):
            sheet = data[position:position + self.tglp['sheet']['size']]
            if format_ == Format.ETC1 or format_ == Format.ETC1A4:
                bmp_data = self._decompress_etc1(sheet)
            else:
                bmp_data = self._sheet_to_bitmap(sheet)
            self.tglp['sheets'].append({
                'width': self.tglp['sheet']['width'],
                'height': self.tglp['sheet']['height'],
                'data': bmp_data
            })
            position = position + self.tglp['sheet']['size']

    def _decompress_etc1(self, data):
        width = self.tglp['sheet']['width']
        height = self.tglp['sheet']['height']

        with_alpha = self.tglp['sheet']['format'] == Format.ETC1A4

        block_size = 16 if with_alpha else 8

        bmp = [[0, 0, 0, 0]] * width * height

        tile_width = int(math.ceil(width / 8.0))
        tile_height = int(math.ceil(height / 8.0))

        # here's the kicker: there will always be a power-of-two amount of tiles
        tile_width = 1 << int(math.ceil(math.log(tile_width, 2)))
        tile_height = 1 << int(math.ceil(math.log(tile_height, 2)))

        pos = 0

        # texture is composed of 8x8 tiles
        for tile_y in range(tile_height):
            for tile_x in range(tile_width):

                # in ETC1 mode each tile is composed of 2x2, compressed sub-tiles, 4x4 pixels each
                for block_y in range(2):
                    for block_x in range(2):
                        data_pos = pos
                        pos += block_size

                        block = data[data_pos:data_pos + block_size]

                        alphas = 0xFFffFFffFFffFFff
                        if with_alpha:
                            alphas = struct.unpack('%sQ' % self.order, block[:8])[0]
                            block = block[8:]

                        pixels = struct.unpack('%sQ' % self.order, block)[0]

                        # how colors are stored in the high-order 32 bits
                        differential = (pixels >> ETC_DIFFERENTIAL_BIT) & 0x01 == 1
                        # how the sub blocks are divided, 0 = 2x4, 1 = 4x2
                        horizontal = (pixels >> ETC_ORIENTATION_BIT) & 0x01 == 1
                        # once the colors are decoded for the sub block this determines how to shift the colors
                        # which modifier row to use for sub block 1
                        table1 = ETC_MODIFIERS[(pixels >> ETC_TABLE1_OFFSET) & 0x07]
                        # which modifier row to use for sub block 2
                        table2 = ETC_MODIFIERS[(pixels >> ETC_TABLE2_OFFSET) & 0x07]

                        color1 = [0, 0, 0]
                        color2 = [0, 0, 0]

                        if differential:
                            # grab the 5-bit code words
                            r = ((pixels >> ETC_DIFF_RED1_OFFSET) & 0x1F)
                            g = ((pixels >> ETC_DIFF_GREEN1_OFFSET) & 0x1F)
                            b = ((pixels >> ETC_DIFF_BLUE_OFFSET) & 0x1F)

                            # extends from 5 to 8 bits by duplicating the 3 most significant bits
                            color1[0] = (r << 3) | ((r >> 2) & 0x07)
                            color1[1] = (g << 3) | ((g >> 2) & 0x07)
                            color1[2] = (b << 3) | ((b >> 2) & 0x07)

                            # add the 2nd block, 3-bit code words to the original words (2's complement!)
                            r += self._complement((pixels >> ETC_RED2_OFFSET) & 0x07, 3)
                            g += self._complement((pixels >> ETC_GREEN2_OFFSET) & 0x07, 3)
                            b += self._complement((pixels >> ETC_BLUE2_OFFSET) & 0x07, 3)

                            # extend from 5 to 8 bits like before
                            color2[0] = (r << 3) | ((r >> 2) & 0x07)
                            color2[1] = (g << 3) | ((g >> 2) & 0x07)
                            color2[2] = (b << 3) | ((b >> 2) & 0x07)
                        else:
                            # 4 bits per channel, 16 possible values

                            # 1st block
                            color1[0] = ((pixels >> ETC_INDIV_RED1_OFFSET) & 0x0F) * 0x11
                            color1[1] = ((pixels >> ETC_INDIV_GREEN1_OFFSET) & 0x0F) * 0x11
                            color1[2] = ((pixels >> ETC_INDIV_BLUE1_OFFSET) & 0x0F) * 0x11

                            # 2nd block
                            color2[0] = ((pixels >> ETC_RED2_OFFSET) & 0x0F) * 0x11
                            color2[1] = ((pixels >> ETC_GREEN2_OFFSET) & 0x0F) * 0x11
                            color2[2] = ((pixels >> ETC_BLUE2_OFFSET) & 0x0F) * 0x11

                        # now that we have two sub block pixel colors to start from,
                        # each pixel is read as a modifier value

                        # 16 pixels are described with 2 bits each,
                        # one selecting the sign, the second the value

                        amounts = pixels & 0xFFFF
                        signs = (pixels >> 16) & 0xFFFF

                        for pixel_y in range(4):
                            for pixel_x in range(4):
                                x = pixel_x + (block_x * 4) + (tile_x * 8)
                                y = pixel_y + (block_y * 4) + (tile_y * 8)

                                if x >= width:
                                    continue
                                if y >= height:
                                    continue

                                offset = pixel_x * 4 + pixel_y

                                if horizontal:
                                    table = table1 if pixel_y < 2 else table2
                                    color = color1 if pixel_y < 2 else color2
                                else:
                                    table = table1 if pixel_x < 2 else table2
                                    color = color1 if pixel_x < 2 else color2

                                # determine the amount to shift the color
                                amount = table[(amounts >> offset) & 0x01]
                                # and in which direction. 1 = -, 0 = +
                                sign = (signs >> offset) & 0x01

                                if sign == 1:
                                    amount *= -1

                                red = max(min(color[0] + amount, 0xFF), 0)
                                green = max(min(color[1] + amount, 0xFF), 0)
                                blue = max(min(color[2] + amount, 0xFF), 0)
                                alpha = ((alphas >> (offset * 4)) & 0x0F) * 0x11

                                pixel_pos = y * width + x

                                bmp[pixel_pos] = [red, green, blue, alpha]
        return bmp

    def _complement(self, input_, bits):
        if input_ >> (bits - 1) == 0:
            return input_
        return input_ - (1 << bits)

    def visit_pixels(
        self,
        vistor: Callable[[
            Format, # format
            List[Tuple[int, int, int, int]], int, # bmp + pos
            bytes, int, # data + pos
        ], None],
        width: int,
        height: int,
        format_: Format,
        bmp: List[Tuple[int, int, int, int]],
        sheet_data: bytes,
    ):
        tile_width = width // 8
        tile_height = height // 8

        # sheet is composed of 8x8 pixel tiles
        for tile_y in range(tile_height):
            for tile_x in range(tile_width):

                # tile is composed of 2x2 sub-tiles
                for y in range(2):
                    for x in range(2):

                        # sub-tile is composed of 2x2 pixel groups
                        for y2 in range(2):
                            for x2 in range(2):

                                # pixel group is composed of 2x2 pixels (finally)
                                for y3 in range(2):
                                    for x3 in range(2):
                                        pixel_x = (x3 + (x2 * 2) + (x * 4) + (tile_x * 8))
                                        pixel_y = (y3 + (y2 * 2) + (y * 4) + (tile_y * 8))

                                        data_x = (x3 + (x2 * 4) + (x * 16) + (tile_x * 64))
                                        data_y = ((y3 * 2) + (y2 * 8) + (y * 32) + (tile_y * width * 8))

                                        sheet_data_pos = data_x + data_y
                                        bmp_pos = pixel_x + (pixel_y * width)

                                        if bmp_pos >= len(bmp):
                                            continue
                                        if (sheet_data_pos * format_.format_size() // 8) >= len(sheet_data):
                                            continue

                                        vistor(format_, bmp, bmp_pos, sheet_data, sheet_data_pos)

    def _sheet_to_bitmap(self, sheet_data):
        width = self.tglp['sheet']['width']
        height = self.tglp['sheet']['height']
        format_: Format = self.tglp['sheet']['format']

        # increase the size of the image to a power-of-two boundary, if necessary
        width = 1 << int(math.ceil(math.log(width, 2)))
        height = 1 << int(math.ceil(math.log(height, 2)))

        # initialize empty bitmap memory (RGBA8)
        bmp: List[Tuple[int, int, int, int]] = [(0, 0, 0, 0)] * (width * height)

        def vistor(
            format: Format,
            bmp: List[Tuple[int, int, int, int]], bmp_pos: int,
            sheet_data: bytes, sheet_data_pos: int
        ):
            bmp[bmp_pos] = self._get_pixel_data(format, sheet_data, sheet_data_pos)

        self.visit_pixels(vistor, width, height, format_, bmp, sheet_data)

        return bmp

    def _get_pixel_data(self, format_, data: bytes, index: int) -> Tuple[int, int, int, int]:
        red = green = blue = alpha = 0

        # rrrrrrrr gggggggg bbbbbbbb aaaaaaaa
        if format_ == Format.RGBA8:
            red, green, blue, alpha = struct.unpack('4B', data[index * 4:index * 4 + 4])

        # rrrrrrrr gggggggg bbbbbbbb
        elif format_ == Format.RGB8:
            red, green, blue = struct.unpack('3B', data[index * 3:index * 3 + 3])
            alpha = 255

        # rrrrrgg gggbbbbba
        elif format_ == Format.RGBA5551:
            b1, b2 = struct.unpack('2B', data[index * 2:index * 2 + 2])

            red = ((b1 >> 3) & 0x1F)
            green = (b1 & 0x07) | ((b2 >> 6) & 0x03)
            blue = (b2 >> 1) & 0x1F
            alpha = (b2 & 0x01) * 255

        # rrrrrggg gggbbbbb
        elif format_ == Format.RGB565:
            b1, b2 = struct.unpack('2B', data[index * 2:index * 2 + 2])

            red = (b1 >> 3) & 0x1F
            green = (b1 & 0x7) | ((b2 >> 5) & 0x7)
            blue = (b2 & 0x1F)
            alpha = 255

        # rrrrgggg bbbbaaaa
        elif format_ == Format.RGBA4:
            b1, b2 = struct.unpack('2B', data[index * 2:index * 2 + 2])

            red = ((b1 >> 4) & 0x0F) * 0x11
            alpha = (b1 & 0x0F) * 0x11
            blue = ((b2 >> 4) & 0x0F) * 0x11
            green = (b2 & 0x0F) * 0x11

        # llllllll aaaaaaaa
        elif format_ == Format.LA8:
            l, alpha = struct.unpack('2B', data[index * 2:index * 2 + 2])
            red = green = blue = l

        # ??
        elif format_ == Format.HILO8:
            # TODO
            pass

        # llllllll
        elif format_ == Format.L8:
            red = green = blue = struct.unpack('B', data[index:index + 1])[0]
            alpha = 255

        # aaaaaaaa
        elif format_ == Format.A8:
            alpha, = struct.unpack('B', data[index:index + 1])
            red = green = blue = 255

        # llllaaaa
        elif format_ == Format.LA4:
            la, = struct.unpack('B', data[index:index + 1])
            red = green = blue = ((la >> 4) & 0x0F) * 0x11
            alpha = (la & 0x0F) * 0x11

        # llll
        elif format_ == Format.L4:
            l = data[index // 2]
            if index & 1 == 1:
                l >>= 4
            l &= 0x0F

            red = green = blue = l * 0x11
            alpha = 255

        # aaaa
        elif format_ == Format.A4:
            a = data[index // 2]
            if index & 1 == 1:
                a >>= 4
            a &= 0x0F

            alpha = a * 0x11
            green = red = blue = 0xFF

        return red, green, blue, alpha

    def _bitmap_to_sheet(self, bmp: List[Tuple[int, int, int, int]]) -> bytes:
        width = self.tglp['sheet']['width']
        height = self.tglp['sheet']['height']
        format_: Format = self.tglp['sheet']['format']

        # increase the size of the image to a power-of-two boundary, if necessary
        width = 1 << int(math.ceil(math.log(width, 2)))
        height = 1 << int(math.ceil(math.log(height, 2)))

        sheet_data: bytes = [0] * self.tglp['sheet']['size']

        def vistor(_, bmp, bmp_pos, sheet_data, sheet_data_pos):
            # OR the data since there are pixel formats which use the same byte for
            # multiple pixels (A4/L4)
            bytes_ = self._get_tglp_pixel_data(bmp, bmp_pos, format_)
            if len(bytes_) > 1:
                sheet_data[sheet_data_pos:sheet_data_pos + len(bytes_)] = bytes_
            else:
                if format_.format_size() == 4:
                    sheet_data_pos //= 2
                sheet_data[sheet_data_pos] |= bytes_[0]

        self.visit_pixels(vistor, width, height, format_, bmp, sheet_data)

        return sheet_data

    def _get_tglp_pixel_data(self, bmp: List[Tuple[int, int, int, int]], index: int, format_) -> List[int]:
        red, green, blue, alpha = bmp[index]

        match format_:
            case Format.RGBA8:
                return [red, green, blue, alpha]

            case Format.RGB8:
                return [red, green, blue]

            # rrrrrggg ggbbbbba
            case Format.RGBA5551:
                r5 = (red // 8) & 0x1F
                g5 = (green // 8) & 0x1F
                b5 = (blue // 8) & 0x1F
                a = 1 if alpha > 0 else 0

                b1 = (r5 << 3) | (g5 >> 2)
                b2 = ((g5 << 6) | (b5 << 1) | a) & 0xFF
                return [b1, b2]

            # rrrrrggg gggbbbbb
            case Format.RGB565:
                r5 = (red // 8) & 0x1F
                g6 = (green // 4) & 0x3F
                b5 = (blue // 8) & 0x1F

                b1 = (r5 << 3) | (g6 >> 3)
                b2 = ((g6 << 5) | b5) & 0xFF
                return [b1, b2]

            # rrrrgggg bbbbaaaa
            case Format.RGBA4:
                r4 = (red // 0x11) & 0x0F
                g4 = (green // 0x11) & 0x0F
                b4 = (blue // 0x11) & 0x0F
                a4 = (alpha // 0x11) & 0x0F

                b1 = (r4 << 4) | g4
                b2 = (b4 << 4) | a4
                return [b1, b2]

            # llllllll aaaaaaaa
            case Format.LA8:
                l = int((red * 0.2126) + (green * 0.7152) + (blue * 0.0722))
                return [l, alpha]

            # TODO
            case Format.HILO8:
                assert_never(format_)

            # llllllll
            case Format.L8:
                l = int((red * 0.2126) + (green * 0.7152) + (blue * 0.0722))
                return [l]

            # aaaaaaaa
            case Format.A8:
                return [alpha]

            # llllaaaa
            case Format.LA4:
                l = int((red * 0.2126) + (green * 0.7152) + (blue * 0.0722)) // 0x11
                a = (alpha // 0x11) & 0x0F

                b = (l << 4) | a
                return [b]

            # llll
            case Format.L4:
                l = int((red * 0.2126) + (green * 0.7152) + (blue * 0.0722))
                shift = (index & 1) * 4
                return [l << shift]

            # aaaa
            case Format.A4:
                alpha = (alpha // 0x11) & 0xF
                shift = (index & 1) * 4
                return [alpha << shift]

            case _:
                assert_never(format_)

    def _parse_cwdh_header(self, data) -> Optional[int]:
        magic, section_size, start_index, end_index, next_cwdh_offset \
            = struct.unpack(CWDH_HEADER_STRUCT % self.order, data)

        if magic != CWDH_HEADER_MAGIC:
            print('Invalid CWDH magic bytes: %s (expected %s)' % (magic, CWDH_HEADER_MAGIC))
            self.invalid = True
            return

        self.cwdh_sections.append(CwdhSection(
            size=section_size,
            start=start_index,
            end=end_index,
            data=[],
        ))

        if self.debug:
            print('CWDH Magic: %s' % magic)
            print('CWDH Section Size: %d' % section_size)
            print('CWDH Start Index: %d' % start_index)
            print('CWDH End Index: %d' % end_index)
            print('CWDH Next CWDH Offset: 0x%x\n' % next_cwdh_offset)

        return next_cwdh_offset

    def _parse_cwdh_data(self, info: CwdhSection, data: bytes):
        count = info.end - info.start + 1
        output: List[CwdhSectionData] = []
        position: int = 0
        for _ in range(count):
            left, glyph, char = struct.unpack('%sb2B' % self.order, data[position:position + 3])
            position += 3
            output.append(CwdhSectionData(left=left, glyph=glyph, char=char))
        info.data = output

    def _parse_cmap_header(self, data):
        magic, section_size, code_begin, code_end, map_method, unknown, next_cmap_offset \
            = struct.unpack(CMAP_HEADER_STRUCT % self.order, data)

        if magic != CMAP_HEADER_MAGIC:
            print('Invalid CMAP magic bytes: %s (expected %s)' % (magic, CMAP_HEADER_MAGIC))

        self.cmap_sections.append({
            'size': section_size,
            'start': code_begin,
            'end': code_end,
            'type': map_method
        })

        if self.debug:
            print('CMAP Magic: %s' % magic)
            print('CMAP Section Size: %d' % section_size)
            print('CMAP Code Begin: 0x%x' % code_begin)
            print('CMAP Code End: 0x%x' % code_end)
            print('CMAP Mapping Method: 0x%x (%s)' % (map_method, MAPPING_METHODS[map_method]))
            print('CMAP Next CMAP Offset: 0x%x' % next_cmap_offset)

            print('\nCMAP Unknown: 0x%x\n' % unknown)

        return next_cmap_offset

    def _parse_cmap_data(self, info, data):
        if self.verbose:
            print('\nParsing CMAP...')
        type_ = info['type']
        if type_ == MAPPING_DIRECT:
            info['indexOffset'] = struct.unpack('%sH' % self.order, data[:2])[0]

        elif type_ == MAPPING_TABLE:
            count = info['end'] - info['start'] + 1
            position = 0
            output = []
            for _ in range(count):
                offset = struct.unpack('%sH' % self.order, data[position:position + 2])[0]
                position += 2
                output.append(offset)
            info['indexTable'] = output

        elif type_ == MAPPING_SCAN:
            position = 0
            count = struct.unpack('%sH' % self.order, data[position:position + 2])[0]
            position += 2
            output = {}
            for _ in range(count):
                code, offset = struct.unpack('%s2H' % self.order, data[position:position + 4])
                position += 4
                output[chr(code)] = offset
            info['entries'] = output


def prompt_yes_no(prompt):
    answer_ = None
    while answer_ not in ('y', 'n'):
        if answer_ is not None:
            print('Please answer "y" or "n"')
        answer_ = input(prompt).lower()

        if len(answer_) == 0:
            answer_ = 'n'

    return answer_


app = typer.Typer(
    context_settings={
        'help_option_names': ['-h', '--help']
    },
    add_completion=False
)

@app.command()
def main(
    verbose:       bool = Opt(False, '-v', '--verbose',       help='print more data when working'),
    debug:         bool = Opt(False, '-d', '--debug',         help='print debug information'),
    yes:           bool = Opt(False, '-y', '--yes',           help='answer yes to any questions (overwriting files)'),
    ensure_ascii:  bool = Opt(True,  '-a', '--ensure-ascii',  help='turn off ensure_ascii option when dump json file'),
    # these two are exclusive
    little_endian: bool = Opt(False, '-l', '--little-endian', help='Use little endian encoding in the created BFFNT file\n[default]'),
    big_endian:    bool = Opt(False, '-b', '--big-endian',    help='Use big endian encoding in the created BFFNT file'),
    # these two are exclusive and required
    create:        bool = Opt(False, '-c', '--create',        help='create BFFNT file from extracted files'),
    extract:       bool = Opt(False, '-x', '--extract',       help='extract BFFNT into PNG/JSON files'),
    file:          str  = Opt(...,   '-f', '--file',          help='BFFNT file', metavar='bffnt'),
):
    """
    BFFNT Converter Tool
    """

    # ensure exclusive
    if little_endian and big_endian:
        raise typer.BadParameter(f"--little-endian is mutually exclusive with --big-endian")

    # ensure required and exclusive
    if create and extract:
        raise typer.BadParameter(f"--create is mutually exclusive with --extract")
    elif not create and not extract:
        raise typer.BadParameter(f"--create or --extract is required")

    if extract and not os.path.exists(file):
        print('Could not find BFFNT file:')
        print(file)
        sys.exit(1)

    basename = os.path.splitext(os.path.basename(file))[0]
    json_file = '%s_manifest.json' % basename

    if extract and os.path.exists(json_file) and not yes:
        print('JSON output file exists.')
        answer = prompt_yes_no('Overwrite? (y/N) ')

        if answer == 'n':
            print('Aborted')
            sys.exit(1)

    sheet_file = '%s_sheet0.png' % basename

    if extract and os.path.exists(sheet_file) and not yes:
        print('At least one sheet PNG file exists.')
        answer = prompt_yes_no('Overwrite? (y/N) ')

        if answer == 'n':
            print('Aborted')
            sys.exit(1)

    if create and os.path.exists(file) and not yes:
        print('BFFNT output file exists.')
        answer = prompt_yes_no('Overwrite? (y/N) ')

        if answer == 'n':
            print('Aborted')
            sys.exit(1)

    if big_endian:
        order = '>'
    else:
        order = '<'

    bffnt = Bffnt(load_order=order, verbose=verbose, debug=debug)

    if extract:
        bffnt.read(file)
        if bffnt.invalid:
            exit(1)
        bffnt.extract(ensure_ascii)
    elif create:
        bffnt.load(json_file)
        if bffnt.invalid:
            exit(1)
        bffnt.save(file)

if __name__ == '__main__':
    app()
