import zipfile
import tempfile
import os
import shutil
import re
from io import BytesIO


def convert_image_to_baseline(image_data, quality=85):
    from PIL import Image

    try:
        img = Image.open(BytesIO(image_data))

        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            if img.mode in ('RGBA', 'LA'):
                background.paste(img, mask=img.split()[-1])
                img = background
            else:
                img = img.convert('RGB')
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        output = BytesIO()
        img.save(output, format='JPEG', quality=quality, progressive=False, optimize=False)
        return output.getvalue()
    except Exception:
        return None


def convert_epub_images(epub_path, output_path=None, quality=85, logger=None):
    if output_path is None:
        output_path = epub_path

    converted_count = 0
    renamed_files = {}

    temp_fd, temp_path = tempfile.mkstemp(suffix='.epub')
    os.close(temp_fd)

    try:
        with zipfile.ZipFile(epub_path, 'r') as zin:
            for item in zin.infolist():
                lower_name = item.filename.lower()
                if lower_name.endswith(('.png', '.gif', '.webp', '.bmp')):
                    base_name = item.filename.rsplit('.', 1)[0]
                    new_name = base_name + '.jpg'
                    renamed_files[item.filename] = new_name

            with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    filename = item.filename
                    lower_name = filename.lower()

                    if lower_name.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')):
                        new_data = convert_image_to_baseline(data, quality)
                        if new_data:
                            data = new_data
                            converted_count += 1
                            if filename in renamed_files:
                                filename = renamed_files[filename]
                                if logger:
                                    logger(f'[Baseline JPEG] Converted: {item.filename} -> {filename}')

                    elif lower_name.endswith(('.xhtml', '.html', '.htm', '.css', '.ncx')):
                        try:
                            text = data.decode('utf-8')
                            for old_name, new_name in renamed_files.items():
                                old_basename = old_name.split('/')[-1]
                                new_basename = new_name.split('/')[-1]
                                text = text.replace(old_basename, new_basename)
                                text = text.replace(old_name, new_name)
                            data = text.encode('utf-8')
                        except Exception:
                            pass

                    elif lower_name.endswith('.opf'):
                        try:
                            text = data.decode('utf-8')
                            for old_name, new_name in renamed_files.items():
                                old_basename = old_name.split('/')[-1]
                                new_basename = new_name.split('/')[-1]
                                text = text.replace(old_basename, new_basename)
                                text = text.replace(old_name, new_name)
                            text = re.sub(
                                r'href="([^"]+\.jpg)"([^>]*)media-type="image/(png|gif|webp|bmp)"',
                                r'href="\1"\2media-type="image/jpeg"',
                                text
                            )
                            text = re.sub(
                                r'media-type="image/(png|gif|webp|bmp)"([^>]*)href="([^"]+\.jpg)"',
                                r'media-type="image/jpeg"\2href="\3"',
                                text
                            )
                            data = text.encode('utf-8')
                        except Exception:
                            pass

                    if item.filename == 'mimetype':
                        zout.writestr(item, data, compress_type=zipfile.ZIP_STORED)
                    else:
                        new_info = zipfile.ZipInfo(filename)
                        new_info.compress_type = zipfile.ZIP_DEFLATED
                        zout.writestr(new_info, data)

        shutil.move(temp_path, output_path)

    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e

    return converted_count
