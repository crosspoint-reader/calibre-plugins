"""
EPUB Image Converter for CrossPoint Reader

Converts EPUB images to baseline JPEG format with various optimizations
for e-reader compatibility.

Features:
- Convert PNG/GIF/WebP/BMP to baseline JPEG
- Fix SVG covers for e-readers
- Scale large images to fit screen
- Light Novel Mode: rotate wide images and split into pages
- Configurable JPEG quality
"""

import io
import os
import re
import zipfile
from contextlib import contextmanager

# Pillow is bundled with Calibre
from PIL import Image


class EpubConverter:
    """Convert EPUB images to baseline JPEG format."""
    
    def __init__(self, 
                 jpeg_quality=85,
                 max_width=480,
                 max_height=800,
                 enable_split_rotate=False,
                 overlap=0.15,
                 logger=None):
        """
        Initialize converter.
        
        Args:
            jpeg_quality: JPEG quality 1-95 (default 85)
            max_width: Maximum image width in pixels (default 480)
            max_height: Maximum image height in pixels (default 800)
            enable_split_rotate: Enable Light Novel Mode (default False)
            overlap: Overlap percentage for split images (default 0.15)
            logger: Optional logging function
        """
        self.jpeg_quality = max(1, min(95, jpeg_quality))
        self.max_width = max_width
        self.max_height = max_height
        self.enable_split_rotate = enable_split_rotate
        self.overlap = overlap
        self._log = logger or (lambda x: None)
        
        # Statistics
        self.stats = {
            'images_converted': 0,
            'svg_covers_fixed': 0,
            'images_split': 0,
            'original_size': 0,
            'new_size': 0,
        }
    
    def convert_epub(self, input_path, output_path=None):
        """
        Convert an EPUB file.
        
        Args:
            input_path: Path to input EPUB file
            output_path: Path to output EPUB file (default: input_baseline.epub)
            
        Returns:
            Path to converted EPUB file
        """
        if output_path is None:
            base, ext = os.path.splitext(input_path)
            output_path = f"{base}_baseline{ext}"
        
        # Reset stats
        self.stats = {
            'images_converted': 0,
            'svg_covers_fixed': 0,
            'images_split': 0,
            'original_size': os.path.getsize(input_path),
            'new_size': 0,
        }
        
        # Track renamed files and split images
        renamed = {}  # old_path -> new_path
        split_images = {}  # orig_name -> [{'path', 'imgName', 'id'}, ...]
        xhtml_files = {}  # path -> content
        opf_path = None
        opf_content = None
        
        self._log(f"Converting: {os.path.basename(input_path)}")
        self._log(f"Quality: {self.jpeg_quality}%")
        self._log(f"Light Novel Mode: {'ON' if self.enable_split_rotate else 'OFF'}")
        
        with zipfile.ZipFile(input_path, 'r') as zin:
            # Build rename map for non-JPEG images
            for name in zin.namelist():
                low = name.lower()
                if re.match(r'.*\.(png|gif|webp|bmp|jpeg)$', low):
                    new_name = re.sub(r'\.(png|gif|webp|bmp|jpeg)$', '.jpg', name, flags=re.IGNORECASE)
                    renamed[name] = new_name
            
            with zipfile.ZipFile(output_path, 'w') as zout:
                # CRITICAL: Write mimetype FIRST per EPUB OCF spec
                # It must be uncompressed and the first entry in the archive
                if 'mimetype' in zin.namelist():
                    zout.writestr('mimetype', zin.read('mimetype'), compress_type=zipfile.ZIP_STORED)
                
                # First pass: process images
                for name in zin.namelist():
                    if name == 'mimetype':
                        continue  # Already written
                    low = name.lower()
                    
                    if re.match(r'.*\.(png|gif|webp|bmp|jpg|jpeg)$', low):
                        data = zin.read(name)
                        parts = self._process_image(data, name)
                        
                        base_name = re.sub(r'\.[^.]+$', '', name)
                        
                        if len(parts) == 1 and parts[0]['suffix'] == '':
                            # Single image, no split
                            new_path = renamed.get(name, re.sub(r'\.[^.]+$', '.jpg', name))
                            zout.writestr(new_path, parts[0]['data'], 
                                         compress_type=zipfile.ZIP_DEFLATED)
                            self.stats['images_converted'] += 1
                        else:
                            # Split image
                            orig_name = os.path.basename(name)
                            split_images[orig_name] = []
                            
                            for part in parts:
                                part_name = os.path.basename(base_name) + part['suffix'] + '.jpg'
                                if '/' in name:
                                    part_path = name[:name.rfind('/') + 1] + part_name
                                else:
                                    part_path = part_name
                                
                                zout.writestr(part_path, part['data'],
                                             compress_type=zipfile.ZIP_DEFLATED)
                                split_images[orig_name].append({
                                    'path': part_path,
                                    'imgName': part_name,
                                    'id': os.path.basename(base_name) + part['suffix']
                                })
                                self.stats['images_converted'] += 1
                            
                            self.stats['images_split'] += len(parts) - 1
                    
                    elif re.match(r'.*\.(xhtml|html|htm)$', low):
                        xhtml_files[name] = zin.read(name).decode('utf-8', errors='ignore')
                    
                    elif low.endswith('.opf'):
                        opf_path = name
                        opf_content = zin.read(name).decode('utf-8', errors='ignore')
                
                # Second pass: update XHTML files
                for path, content in xhtml_files.items():
                    t = content
                    
                    # Fix SVG covers
                    fixed_svg = self._fix_svg_cover(t)
                    if fixed_svg['fixed']:
                        t = fixed_svg['content']
                        self.stats['svg_covers_fixed'] += 1
                    
                    # Update image references
                    for old, new in renamed.items():
                        old_name = os.path.basename(old)
                        new_name = os.path.basename(new)
                        t = t.replace(old_name, new_name)
                    
                    # Update split image references
                    for orig_name, parts in split_images.items():
                        new_name = re.sub(r'\.(png|gif|webp|bmp|jpeg)$', '.jpg', orig_name, flags=re.IGNORECASE)
                        
                        # Replace block patterns (p/div with span and img)
                        block_pattern = re.compile(
                            r'(<(?:p|div)[^>]*>\s*<span>\s*<img[^>]*src=["\'][^"\']*(?:' + 
                            re.escape(orig_name) + '|' + re.escape(new_name) + 
                            r')[^>]*/?>\s*</span>\s*</(?:p|div)>)',
                            re.IGNORECASE | re.DOTALL
                        )
                        
                        # Bind loop variables via default arguments to avoid B023
                        def replace_block(match, parts=parts, orig_name=orig_name, new_name=new_name):
                            result = []
                            for i, part in enumerate(parts):
                                if i > 0:
                                    result.append('\n')
                                new_block = match.group(0).replace(orig_name, part['imgName'])
                                new_block = new_block.replace(new_name, part['imgName'])
                                result.append(new_block)
                            return ''.join(result)
                        
                        t = block_pattern.sub(replace_block, t)
                        
                        # Replace simple img patterns
                        simple_pattern = re.compile(
                            r'(<img[^>]*src=["\'])([^"\']*(?:' + 
                            re.escape(orig_name) + '|' + re.escape(new_name) + 
                            r'))([^>]*/>)',
                            re.IGNORECASE
                        )
                        
                        # Bind loop variables via default arguments to avoid B023
                        def replace_simple(match, parts=parts, orig_name=orig_name, new_name=new_name):
                            result = []
                            for i, part in enumerate(parts):
                                if i > 0:
                                    result.append('\n')
                                new_src = match.group(2).replace(orig_name, part['imgName'])
                                new_src = new_src.replace(new_name, part['imgName'])
                                result.append(match.group(1) + new_src + match.group(3))
                            return ''.join(result)
                        
                        t = simple_pattern.sub(replace_simple, t)
                    
                    zout.writestr(path, t.encode('utf-8'), 
                                 compress_type=zipfile.ZIP_DEFLATED)
                
                # Third pass: update OPF
                if opf_content:
                    t = opf_content
                    
                    # Update image references
                    for old, new in renamed.items():
                        old_name = os.path.basename(old)
                        new_name = os.path.basename(new)
                        t = t.replace(old_name, new_name)
                    
                    # Fix media-types for converted images
                    t = re.sub(
                        r'href="([^"]+\.jpg)"([^>]*)media-type="image/(png|gif|webp|bmp)"',
                        r'href="\1"\2media-type="image/jpeg"',
                        t
                    )
                    t = re.sub(
                        r'media-type="image/(png|gif|webp|bmp)"([^>]*)href="([^"]+\.jpg)"',
                        r'media-type="image/jpeg"\2href="\3"',
                        t
                    )
                    
                    # Update split image references in OPF
                    for orig_name, parts in split_images.items():
                        orig_base = re.sub(r'\.[^.]+$', '', orig_name)
                        
                        # Update original reference to part1
                        pattern = re.compile(
                            r'(href=["\'][^"\']*/?)('+re.escape(orig_base)+r')\.(?:jpg|jpeg|png|gif|webp|bmp)(["\'])',
                            re.IGNORECASE
                        )
                        t = pattern.sub(r'\g<1>' + orig_base + r'_part1.jpg\3', t)
                        
                        # Add manifest entries for additional parts
                        manifest_additions = ''
                        for j in range(1, len(parts)):
                            p = parts[j]
                            manifest_additions += f'<item id="img-{p["id"]}" href="{p["imgName"]}" media-type="image/jpeg"/>\n'
                        
                        if manifest_additions:
                            t = t.replace('</manifest>', manifest_additions + '</manifest>')
                    
                    # Ensure cover meta
                    fixed_cover = self._ensure_cover_meta(t)
                    if fixed_cover['fixed']:
                        t = fixed_cover['content']
                        self._log("Fixed cover meta")
                    
                    zout.writestr(opf_path, t.encode('utf-8'),
                                 compress_type=zipfile.ZIP_DEFLATED)
                
                # Fourth pass: copy remaining files
                for name in zin.namelist():
                    if name == 'mimetype':
                        continue  # Already written first
                    low = name.lower()
                    
                    # Skip already processed files
                    if re.match(r'.*\.(png|gif|webp|bmp|jpg|jpeg)$', low):
                        continue
                    if re.match(r'.*\.(xhtml|html|htm)$', low):
                        continue
                    if low.endswith('.opf'):
                        continue
                    
                    data = zin.read(name)
                    
                    # Update CSS and NCX references
                    if low.endswith('.css') or low.endswith('.ncx'):
                        text = data.decode('utf-8', errors='ignore')
                        for old, new in renamed.items():
                            old_name = os.path.basename(old)
                            new_name = os.path.basename(new)
                            text = text.replace(old_name, new_name)
                        data = text.encode('utf-8')
                    
                    zout.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        
        self.stats['new_size'] = os.path.getsize(output_path)
        
        saved = self.stats['original_size'] - self.stats['new_size']
        if saved > 0:
            pct = (saved / self.stats['original_size']) * 100
            self._log(f"Converted {self.stats['images_converted']} images")
            self._log(f"Saved {self._format_bytes(saved)} ({pct:.1f}%)")
        else:
            self._log(f"Converted {self.stats['images_converted']} images")
            self._log(f"Size increased by {self._format_bytes(-saved)}")
        
        if self.stats['images_split'] > 0:
            self._log(f"Created {self.stats['images_split']} additional pages from splits")
        if self.stats['svg_covers_fixed'] > 0:
            self._log(f"Fixed {self.stats['svg_covers_fixed']} SVG cover(s)")
        
        return output_path
    
    def _process_image(self, data, name):
        """
        Process a single image.
        
        Returns list of {'data': bytes, 'suffix': str}
        """
        try:
            img = Image.open(io.BytesIO(data))
            
            # Convert to RGB if necessary
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                if img.mode in ('RGBA', 'LA'):
                    background.paste(img, mask=img.split()[-1])
                    img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            orig_w, orig_h = img.size
            
            # Check if horizontal and exceeds screen
            is_horizontal = orig_w > orig_h
            exceeds_screen = orig_w > self.max_width or orig_h > self.max_height
            needs_rotation = is_horizontal and exceeds_screen
            
            if needs_rotation and self.enable_split_rotate:
                return self._process_split_rotate(img, orig_w, orig_h)
            else:
                return self._process_normal(img, orig_w, orig_h)
            
        except Exception as e:
            self._log(f"Error processing {name}: {e}")
            # Return original data as fallback
            return [{'data': data, 'suffix': ''}]
    
    def _process_normal(self, img, orig_w, orig_h):
        """Process image without rotation/split."""
        fits_in_screen = orig_w <= self.max_width and orig_h <= self.max_height
        
        if not fits_in_screen:
            # Scale to fit
            scale = min(self.max_width / orig_w, self.max_height / orig_h)
            new_w = int(orig_w * scale)
            new_h = int(orig_h * scale)
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        
        # Save as baseline JPEG
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=self.jpeg_quality, progressive=False)
        return [{'data': buf.getvalue(), 'suffix': ''}]
    
    def _process_split_rotate(self, img, orig_w, orig_h):
        """Process horizontal image with rotation and optional split."""
        # Step 1: Scale width to max_height (800)
        scale = self.max_height / orig_w
        scaled_w = self.max_height
        scaled_h = int(orig_h * scale)
        
        img = img.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
        
        # Step 2: Rotate 90° clockwise
        img = img.transpose(Image.Transpose.ROTATE_270)
        rot_w, rot_h = img.size
        
        # Step 3: Split if needed
        if rot_w <= self.max_width:
            # No split needed
            buf = io.BytesIO()
            img.save(buf, 'JPEG', quality=self.jpeg_quality, progressive=False)
            return [{'data': buf.getvalue(), 'suffix': ''}]
        else:
            # Split by WIDTH (vertical cuts) - from RIGHT to LEFT
            # After 90° CW rotation: right side becomes top, left becomes bottom
            # So we cut from right to left to get top-to-bottom order
            parts = []
            max_w = self.max_width
            overlap_px = int(max_w * self.overlap)
            step = max_w - overlap_px
            num_parts = (rot_w - overlap_px + step - 1) // step  # ceil division
            
            for i in range(num_parts):
                # Start from right side (rot_w) and go left
                x = rot_w - max_w - (i * step)
                if i == num_parts - 1:
                    x = 0  # Last part starts at left edge
                x = max(0, x)
                
                part_w = min(max_w, rot_w - x)
                
                part_img = img.crop((x, 0, x + part_w, rot_h))
                
                buf = io.BytesIO()
                part_img.save(buf, 'JPEG', quality=self.jpeg_quality, progressive=False)
                parts.append({'data': buf.getvalue(), 'suffix': f'_part{i + 1}'})
            
            return parts
    
    def _fix_svg_cover(self, content):
        """Fix SVG cover to regular HTML img tag."""
        if '<svg' not in content or 'xlink:href' not in content:
            return {'content': content, 'fixed': False}
        
        if ('calibre:cover' not in content and 
            'name="cover"' not in content and 
            '<title>Cover</title>' not in content):
            return {'content': content, 'fixed': False}
        
        match = re.search(r'xlink:href=["\']([^"\']+)["\']', content)
        if not match:
            return {'content': content, 'fixed': False}
        
        img_src = match.group(1)
        new_content = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
<head><meta content="text/html; charset=UTF-8" http-equiv="default-style"/><title>Cover</title></head>
<body><section epub:type="cover"><img alt="Cover" src="{img_src}"/></section></body>
</html>'''
        
        return {'content': new_content, 'fixed': True}
    
    def _ensure_cover_meta(self, content):
        """Ensure OPF has correct cover meta tag."""
        cover_id = None
        
        # Try to find cover image ID
        patterns = [
            r'<item[^>]+id="([^"]+)"[^>]+properties="[^"]*cover-image[^"]*"',
            r'<item[^>]+properties="[^"]*cover-image[^"]*"[^>]+id="([^"]+)"',
            r'<item[^>]+id="([^"]+)"[^>]+href="[^"]*cover[^"]*"[^>]*media-type="image/',
            r'<item[^>]+href="[^"]*cover[^"]*"[^>]+id="([^"]+)"[^>]*media-type="image/',
            r'<item[^>]+id="([^"]*cover[^"]*)"[^>]+media-type="image/',
            r'<item[^>]+media-type="image/[^"]*"[^>]+id="([^"]*cover[^"]*)"',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                cover_id = match.group(1)
                break
        
        if not cover_id:
            return {'content': content, 'fixed': False}
        
        # Check if cover meta exists
        meta_match = (re.search(r'<meta\s+name=["\']cover["\']\s+content=["\']([^"\']+)["\']', content) or
                     re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']cover["\']', content))
        
        if meta_match:
            current_value = meta_match.group(1)
            if '/' in current_value or current_value != cover_id:
                # Fix incorrect cover meta
                content = re.sub(
                    r'<meta\s+name=["\']cover["\']\s+content=["\'][^"\']+["\']\s*/?>',
                    f'<meta name="cover" content="{cover_id}" />',
                    content
                )
                content = re.sub(
                    r'<meta\s+content=["\'][^"\']+["\']\s+name=["\']cover["\']\s*/?>',
                    f'<meta name="cover" content="{cover_id}" />',
                    content
                )
                return {'content': content, 'fixed': True}
            return {'content': content, 'fixed': False}
        
        # Add missing cover meta
        content = content.replace(
            '</metadata>',
            f'    <meta name="cover" content="{cover_id}"/>\n  </metadata>'
        )
        return {'content': content, 'fixed': True}
    
    @staticmethod
    def _format_bytes(b):
        """Format bytes as human-readable string."""
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        else:
            return f"{b / (1024 * 1024 * 1024):.1f} GB"


def convert_epub_file(input_path, output_path=None, **kwargs):
    """
    Convenience function to convert an EPUB file.
    
    Args:
        input_path: Path to input EPUB
        output_path: Path to output EPUB (optional)
        **kwargs: Options passed to EpubConverter
        
    Returns:
        Path to converted EPUB
    """
    converter = EpubConverter(**kwargs)
    return converter.convert_epub(input_path, output_path)
