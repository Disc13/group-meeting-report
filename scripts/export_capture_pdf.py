"""Export readable capture representatives to an indexed image PDF."""
import argparse,json
from pathlib import Path


def write_pdf(root,destination,rows,title='Group meeting - video page captures'):
    """Export explicitly selected rows; per-person times take precedence."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    destination.parent.mkdir(parents=True,exist_ok=True)
    pdf=canvas.Canvas(str(destination),pagesize=(960,568))
    pdf.setTitle(title)
    for row in rows:
        page_id=row['page_id']
        source=root.parent/row['export_image'] if row.get('export_image') else root/row['image']
        image=ImageReader(str(source));w,h=image.getSize()
        scale=min(960/w,540/h);dw,dh=w*scale,h*scale
        pdf.drawImage(image,(960-dw)/2,28+(540-dh)/2,dw,dh)
        reference=row.get('reference') or {}
        shown=row.get('first_shown_in_section_s',row.get('first_shown_s',row['start_s']))
        label='%s | shown %02d:%02d | source PPT page %s | %s'%(page_id,shown//60,shown%60,
                    reference.get('page') or 'unmatched','stable' if row['stable'] else 'short/moving - review')
        pdf.setFont('Helvetica',10);pdf.drawString(14,10,label)
        pdf.bookmarkPage(page_id);pdf.addOutlineEntry(label,page_id,0)
        pdf.showPage()
    pdf.save()


def select_rows(root):
    from select_images import select_rows as images
    return images(root)


def run(root,destination=None):
    pages=select_rows(root)
    if destination:write_pdf(root,destination,pages)
    print(destination or 'Selection only',len(pages),'pages')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('captures',type=Path);p.add_argument('output',type=Path,nargs='?')
    p.add_argument('--selection-only',action='store_true',help='Select pages without authoring a PDF')
    a=p.parse_args()
    if not a.selection_only and not a.output:p.error('output is required unless --selection-only')
    run(a.captures,None if a.selection_only else a.output)
