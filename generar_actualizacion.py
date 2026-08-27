import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import requests
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'actualizacion_monitoreo.json'
HISTORY = ROOT / 'historial_ids.json'
META_OUT = ROOT / 'metadata.json'

URLS = {
    'bid_data': 'https://raw.githubusercontent.com/AluenParadigma/bid-data-public/main/latest.json',
    'bid_meta': 'https://raw.githubusercontent.com/AluenParadigma/bid-data-public/main/metadata.json',
    'sicop_data': 'https://raw.githubusercontent.com/AluenParadigma/sicop-data-public/main/latest.json',
    'sicop_meta': 'https://raw.githubusercontent.com/AluenParadigma/sicop-data-public/main/metadata.json',
    'wb_data': 'https://raw.githubusercontent.com/AluenParadigma/worldbank-data-public/main/data/latest.json',
    'wb_meta': 'https://raw.githubusercontent.com/AluenParadigma/worldbank-data-public/main/data/metadata.json',
}

AFFINITIES = {'Muy alta', 'Alta', 'Media-Alta', 'Media', 'Baja'}
RELEVANT = {'Muy alta', 'Alta', 'Media-Alta', 'Media'}


def get_json(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def validate_bid(data, meta):
    rows = data.get('data', [])
    total = int(meta.get('total_records', -1))
    ok = (
        meta.get('coverage_complete') is True and
        meta.get('validation_status') == 'OK' and
        len(rows) == total
    )
    return ok, total, rows


def validate_sicop(data, meta):
    rows = data.get('data', data if isinstance(data, list) else [])
    te = int(meta.get('total_elements', -1))
    re = int(meta.get('records_extracted', -2))
    cr = int(meta.get('csv_records', -3))
    ok = (
        te == re == cr and
        meta.get('coverage_complete') is True and
        meta.get('validation_status') == 'OK' and
        len(rows) == te
    )
    return ok, te, rows


def validate_wb(data, meta):
    rows = data.get('data', [])
    current = int(meta.get('current_consulting_en_es', -1))
    jr = int(meta.get('json_records', -2))
    cr = int(meta.get('csv_records', -3))
    controls = meta.get('language_controls', {})
    lc_ok = True
    if isinstance(controls, dict):
        vals = controls.values()
    elif isinstance(controls, list):
        vals = controls
    else:
        vals = []
        lc_ok = False
    for c in vals:
        if c.get('coverage_complete') is not True or int(c.get('records_downloaded', -1)) != int(c.get('api_total', -2)):
            lc_ok = False
    ok = (
        lc_ok and current == jr == cr and len(rows) == current and
        meta.get('coverage_complete') is True and
        meta.get('validation_status') == 'OK'
    )
    return ok, current, rows


def compact_record(source, r):
    if source == 'BID':
        return {
            'source': source,
            'id': str(r.get('id') or r.get('tender_id') or r.get('reference') or ''),
            'title': r.get('title'),
            'institution': r.get('organization') or r.get('institution') or r.get('agency'),
            'country': r.get('country'),
            'amount': r.get('amount'),
            'publication_date': r.get('date_created'),
            'deadline': r.get('date_close'),
            'url': r.get('url') or r.get('link') or r.get('source_url'),
            'text': r.get('description') or r.get('content') or r.get('scope') or '',
            'raw_id_candidates': {k: r.get(k) for k in ['id','tender_id','project_id','reference_no','slug'] if r.get(k) is not None},
        }
    if source == 'SICOP':
        return {
            'source': source,
            'id': str(r.get('instCartelNo') or ''),
            'title': r.get('cartelNm') or r.get('title') or r.get('objeto') or r.get('description'),
            'institution': r.get('cartelInstNm'),
            'country': 'Costa Rica',
            'amount': None,
            'publication_date': r.get('publication_date') or r.get('regDt'),
            'deadline': r.get('openbidDt'),
            'url': r.get('url') or r.get('link'),
            'text': r.get('cartelNm') or r.get('description') or '',
            'raw_id_candidates': {'instCartelNo': r.get('instCartelNo')},
        }
    return {
        'source': source,
        'id': str(r.get('notice_id') or r.get('reference_no') or ''),
        'title': r.get('title'),
        'institution': r.get('institution') or r.get('project_name'),
        'country': r.get('country'),
        'amount': None,
        'publication_date': r.get('publication_date'),
        'deadline': r.get('deadline'),
        'url': r.get('source_url'),
        'text': r.get('notice_text') or '',
        'project_id': r.get('project_id'),
        'reference_no': r.get('reference_no'),
        'language': r.get('language'),
        'procurement_method': r.get('procurement_method'),
    }


def classify_batch(client, batch):
    prompt = '''Sos un analista senior de oportunidades comerciales para una consultora de gestión y tecnología llamada Paradigma.
Evaluá SEMÁNTICAMENTE cada registro, sin exigir palabras clave. Afinidad con: consultoría de gestión, estrategia, transformación digital, procesos, eficiencia, modelos operativos, gestión del cambio, PMO, gestión de proyectos/programas/carteras, implementación, tecnología, datos, fortalecimiento institucional, metodologías, gobernanza, capacitación y servicios profesionales relacionados.
Afinidad permitida exclusivamente: Muy alta, Alta, Media-Alta, Media, Baja.
Para cada registro devolvé JSON con: id, affinity, summary. summary debe tener 1-3 oraciones concretas sobre objeto y alcance. No inventes montos, fechas ni alcance.
Respondé únicamente JSON con formato {"items":[...]}.
Registros:\n''' + json.dumps(batch, ensure_ascii=False)
    resp = client.responses.create(
        model='gpt-5-mini',
        input=prompt,
        text={'format': {'type': 'json_object'}},
    )
    obj = json.loads(resp.output_text)
    return obj.get('items', [])


def load_history():
    if HISTORY.exists():
        try:
            return set(json.loads(HISTORY.read_text(encoding='utf-8')).get('ids', []))
        except Exception:
            return set()
    return set()


def key(source, ident):
    return f'{source}|{ident}'.strip()


def main():
    if not os.environ.get('OPENAI_API_KEY'):
        print('Falta el secret OPENAI_API_KEY', file=sys.stderr)
        sys.exit(2)

    bid_data, bid_meta = get_json(URLS['bid_data']), get_json(URLS['bid_meta'])
    sic_data, sic_meta = get_json(URLS['sicop_data']), get_json(URLS['sicop_meta'])
    wb_data, wb_meta = get_json(URLS['wb_data']), get_json(URLS['wb_meta'])

    vb, nb, rb = validate_bid(bid_data, bid_meta)
    vs, ns, rs = validate_sicop(sic_data, sic_meta)
    vw, nw, rw = validate_wb(wb_data, wb_meta)
    if not (vb and vs and vw):
        print(f'Cobertura invalida. BID={vb} SICOP={vs} WB={vw}', file=sys.stderr)
        sys.exit(3)

    all_records = []
    for s, rows in [('BID', rb), ('SICOP', rs), ('Banco Mundial', rw)]:
        all_records.extend(compact_record(s, r) for r in rows)

    client = OpenAI()
    classified = {}
    # Se analiza el 100% en lotes; no hay prefiltrado por keywords.
    batch_size = 25
    for i in range(0, len(all_records), batch_size):
        batch = all_records[i:i+batch_size]
        result = classify_batch(client, batch)
        for x in result:
            classified[str(x.get('id',''))] = x

    history = load_history()
    novedades = []
    all_seen = set(history)
    for r in all_records:
        c = classified.get(r['id'], {})
        aff = c.get('affinity', 'Baja')
        if aff not in AFFINITIES:
            aff = 'Baja'
        k = key(r['source'], r['id'])
        all_seen.add(k)
        if aff in RELEVANT and k not in history:
            amount = r.get('amount')
            if r['source'] == 'BID':
                monto = f'USD {amount}' if amount not in (None, '', 0) else 'No informado'
            else:
                monto = 'No informado'
            novedades.append({
                'Fuente': r['source'],
                'Identificador': r['id'],
                'Oportunidad': r.get('title') or '',
                'Institución/Entidad': r.get('institution') or '',
                'País': r.get('country') or '',
                'Monto': monto,
                'Fecha de publicación': r.get('publication_date') or 'No informado',
                'Cierre': r.get('deadline') or 'No informado',
                'Afinidad': aff,
                'Resumen de lo que piden': c.get('summary') or '',
                'Fuente/Enlace': r.get('url') or '',
            })

    generated = datetime.now(timezone.utc).isoformat()
    payload = {
        'generated_at': generated,
        'coverage': {
            'BID': f'{nb}/{nb}',
            'SICOP': f'{ns}/{ns}',
            'Banco Mundial': f'{nw}/{nw}',
        },
        'novedades': novedades,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    HISTORY.write_text(json.dumps({'ids': sorted(all_seen)}, ensure_ascii=False, indent=2), encoding='utf-8')
    META_OUT.write_text(json.dumps({
        'generated_at': generated,
        'coverage_complete': True,
        'validation_status': 'OK',
        'bid_records': nb,
        'sicop_records': ns,
        'worldbank_records': nw,
        'new_records': len(novedades),
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'OK: {len(novedades)} novedades. Cobertura BID {nb}/{nb}, SICOP {ns}/{ns}, WB {nw}/{nw}')

if __name__ == '__main__':
    main()
