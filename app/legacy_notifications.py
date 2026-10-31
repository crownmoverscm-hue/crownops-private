"""Bounded rendered-notification parsing. Source values never approve prices."""
import re
from datetime import date


def _fields(section, labels):
    # Only fixed labels inside a recognized template section are delimiters.
    pattern = '|'.join(re.escape(x) for x in sorted(labels, key=len, reverse=True))
    matches = list(re.finditer(pattern, section))
    result = {}
    for i, match in enumerate(matches):
        end = matches[i+1].start() if i+1 < len(matches) else len(section)
        result.setdefault(match.group(), []).append(section[match.end():end].strip())
    return result


def parse_notification(text, subject):
    en = subject == 'Quick Quote Form EN' and text.startswith('Quick Quote Form EN')
    fr = subject == 'Form Submission from Free Quote FR Page' and text.startswith(subject)
    calc = subject == 'New Instant Moving Estimate Request - Crown Movers' and 'Client DetailsName' in text and 'Follow-Up Priority' in text
    styled_subject = re.fullmatch(r'New Crown Movers Quote Request - .+ - (\d{4}-\d{2}-\d{2})',subject)
    styled_contact = re.search(r'Client Contact Info(.*?)Move Summary',text,re.S)
    styled = bool(styled_subject and styled_contact and 'WEBSITE LEAD - FREE MOVING QUOTE' in text)
    if not (en or fr or calc or styled):
        return None
    reviews = []
    result = dict.fromkeys(('email','phone','move_date','pickup','dropoff','home_size','boxes',
        'pickup_access','dropoff_access','pickup_walk','dropoff_walk','heavy_items','photo_link','notes','inventory'), '')
    result.update(client_name='Website inquiry', source='Gmail', lead_type='Instant estimate' if calc else 'Form',
        stage='New Lead', priority='Normal', language='French' if fr else 'English',
        intake={'local_area':'','service_details':'','start_window':'','between_stops_minutes':'','pickup_base_minutes':'','return_base_minutes':''})
    def value(fields, label):
        values = list(dict.fromkeys(fields.get(label, [])))
        if len(values) > 1:
            reviews.append('Conflicting values for '+label)
            return ''
        return values[0] if values else ''
    if styled:
        fields = _fields(styled_contact.group(1),['Name:','Phone:','Email:'])
        mapping = {'client_name':'Name:'}
        section = text.split('Move Summary',1)[1]
        section, sep, notes = section.partition('Additional Details:')
        result['notes'] = notes.strip() if sep else ''
        summary = _fields(section,['Moving Date:','Pickup Address:','Destination Address:'])
        result['source_summary_fields'] = summary
        result['source_subject_date'] = styled_subject.group(1)
        result['move_date'] = value(summary,'Moving Date:')
        if len(set(summary.get('Moving Date:',[]))) > 1:
            result['move_date']=''
        elif result['move_date'] and result['move_date'] != styled_subject.group(1):
            result['move_date']=''
            reviews.append('Moving date conflicts between notification body and subject; staff review required')
        elif not result['move_date']:
            result['move_date']=styled_subject.group(1)
            reviews.append('Moving date recovered from subject; confirm missing body date')
        result['pickup'] = value(summary,'Pickup Address:')
        result['dropoff'] = value(summary,'Destination Address:')
        reviews.append('Styled notification language is not an explicit customer preference; confirm with staff')
    elif en:
        section = text
        while section.startswith(subject):
            section = section[len(subject):]
        fields = _fields(section, ['Name','Email','Phone','Moving Date','Moving From (PC)','Moving To (PC)','# of Bedrooms'])
        mapping = {'client_name':'Name','move_date':'Moving Date','pickup':'Moving From (PC)','dropoff':'Moving To (PC)','home_size':'# of Bedrooms'}
    elif fr:
        section = text.split('InfoNom',1)
        if len(section) != 2:
            return None
        section = 'Nom'+section[1]
        section, sep, notes = section.partition('Informations complémentaires')
        result['notes'] = notes.strip() if sep else ''
        fields = _fields(section, ['Nom','Courriel','Téléphone','DateDate du déménagement','DépartAdresse','Ville','Code postal','Ascenseur','Nombre de chambres',
            "Souhaitez-vous ajouter des services d'emballage?","Avez-vous besoin d'un espace de stockage supplémentaire?","Vous avez besoin de services de nettoyage?"])
        mapping = {'client_name':'Nom','move_date':'DateDate du déménagement','home_size':'Nombre de chambres'}
        # This flattened template has no reliable destination section. Do not
        # turn the second postal code (or prose) into a guessed address.
        reviews.append('French form address sections require review; departure address and postal field may conflict; destination is not explicitly labelled')
        result['pickup_access'] = ('Ascenseur: '+value(fields,'Ascenseur')) if value(fields,'Ascenseur') else ''
    else:
        contact = text.split('Client Details',1)[1].split('Follow-Up Priority',1)[0]
        fields = _fields(contact, ['Name','Phone','Email','Moving Date'])
        mapping = {'client_name':'Name','move_date':'Moving Date'}
        details = text.split('Move Details',1)[1] if 'Move Details' in text else ''
        details = details.split('Photos / Video',1)[0]
        details, sep, notes = details.partition('Details / Notes')
        result['notes'] = notes.strip() if sep else ''
        move = _fields(details, ['Pickup','Destination','Greater Montreal?','Home Size','Pickup Access','Drop-off Access','Walk at Pickup','Walk at Drop-off','Boxes','Inventory / Services','Heavy / Specialty Items','Additional Services'])
        for target,label in {'pickup':'Pickup','dropoff':'Destination','home_size':'Home Size','pickup_access':'Pickup Access','dropoff_access':'Drop-off Access',
            'pickup_walk':'Walk at Pickup','dropoff_walk':'Walk at Drop-off','boxes':'Boxes','inventory':'Inventory / Services','heavy_items':'Heavy / Specialty Items'}.items():
            result[target] = value(move,label)
        local = value(move,'Greater Montreal?')
        result['intake']['local_area'] = {'Yes, within Greater Montreal':'Yes','Oui':'Yes','No':'No','Non':'No'}.get(local,'')
        summary = text.split('Quote Summary',1)[1].split('Move Details',1)[0] if 'Quote Summary' in text else ''
        source = _fields(summary,['Estimate Range','Movers','Truck(s)','Move Time','Transportation','Hourly Rate','Gas Fee','Season / Rate Used'])
        result['calculator_source'] = {'approved':False,'values':{k:value(source,k) for k in source}}
        result['source_move_fields'] = move
        reviews.append('Calculator values are unapproved source information; confirm pricing and travel inputs')
        if 'Appartement' in result['home_size'] or local == 'Oui':
            result['language'] = 'French'
        reviews.append('Calculator language inferred from submitted content; no explicit language choice')
        # A photo must be on the actual link line, never the next heading.
        photo = re.search(r'(?m)^Client photo or video link:[ \t]*(https://[^\s]+)?[ \t]*$',text)
        result['photo_link'] = photo.group(1) or '' if photo else ''
    for target,label in mapping.items():
        result[target] = value(fields,label)
    email_label,phone_label = ('Email:','Phone:') if styled else (('Courriel','Téléphone') if fr else ('Email','Phone'))
    emails = {x.lower() for x in re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z])', value(fields,email_label))}
    phones = {re.sub(r'\D','',x) for x in re.findall(r'(?:\+?1[ .-]?)?\(?[2-9]\d{2}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)',value(fields,phone_label))}
    phones = {x[1:] if len(x)==11 and x.startswith('1') else x for x in phones}
    if len(emails)==1:result['email']=emails.pop()
    elif emails:
        reviews.append('Conflicting customer emails; contact requires review')
        result['contact_conflict']=True
    if len(phones)==1:result['phone']=phones.pop()
    elif phones:
        reviews.append('Conflicting customer phones; contact requires review')
        result['contact_conflict']=True
    try:
        result['move_date'] = date.fromisoformat(result['move_date']).isoformat()
    except ValueError:
        if result['move_date']:reviews.append('Invalid submitted moving date')
        result['move_date']=''
    result['source_contact_fields'] = fields
    result['parser_review'] = reviews
    result['intake']['service_details'] = result['notes']
    return result
