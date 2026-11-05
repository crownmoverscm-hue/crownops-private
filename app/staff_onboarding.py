"""Plain-text and HTML staff invitations; no credentials or delivery side effects."""
from html import escape

ROLE_LABELS = {'owner':'Owner','manager':'Manager','sales':'Sales','dispatch':'Dispatch','crew':'Crew','accounting':'Accounting'}
ROLE_STEPS = {
    'owner':[
        'Open Team access to review staff accounts and invitations.',
        'Review the Launch checklist, operating rules and company connections before approving live work.',
        'Keep your owner login private. Give each team member their own account.'
    ],
    'manager':[
        'Open Today to review company work, then use Sales, Schedule, Team and Expenses as needed.',
        'Your Manager account has full company access, including financial and staff information.',
        'Link your employee record to appear in crew assignments and My Jobs. Keep this sign-in when leading a move.'
    ],
    'sales':[
        'Open Sales & clients to review quote requests, missed calls and follow-ups.',
        'Claim the work you are handling and record your next action so the team knows who is responsible.',
        'Check the inventory, addresses, access and timing before preparing an estimate.'
    ],
    'dispatch':[
        'Review Today and the schedule, then check the crew and trucks assigned to each job.',
        'Review truck availability, required checks and job instructions before dispatching.',
        'Use Important contacts for service help and flag missing details to the owner.'
    ],
    'crew':[
        'Open your assigned jobs and read the addresses, timing, access notes and truck instructions.',
        'Complete the required check on the actual truck and report defects to dispatch.',
        'Enter actual hours and materials for your assigned job, then review them with your manager.'
    ],
    'accounting':[
        'Review invoice details, actual time, taxes and deposit credits.',
        'Review submitted hours and the correct payroll period before approval.',
        'Flag mismatched totals to the owner before releasing payroll or exporting accounting records.'
    ]
}

DEVICE_STEPS = [
    ('Samsung / Android', 'Open the regular app address in Chrome. Tap the three-dot menu, then Install and create shortcut > Install. Some versions say Install app or Add to Home screen.'),
    ('iPhone / iPad', 'Open the regular app address in Safari. Tap Share > Add to Home Screen. Turn on Open as Web App if shown, then tap Add.'),
    ('MacBook / Mac', 'Open the regular app address in Safari or Chrome. In Safari on macOS Sonoma 14 or later, choose File > Add to Dock. On an older Mac, bookmark the app and use it in your browser.'),
    ('Windows', 'Open the regular app address in Chrome or Edge. In Chrome, choose the three-dot menu > Cast, save and share > Install page as app. In Edge, choose the three-dot menu > More tools > Apps > Install this site as an app.')
]


def invitation_content(person, public_url, signup_url=None, company=None):
    from .company_profile import profile_or_legacy
    company_name = profile_or_legacy(company)['name']
    """The preview uses a labeled placeholder; only the send path supplies a real link."""
    role = person['role']
    name = person.get('name') or 'Team member'
    email = person.get('email') or 'the email address on your invitation'
    address = public_url.rstrip('/')+'/'
    subject = company_name+': set up your CrownOps account'
    sections = [
        ('1. Create your account', [
            'Open your private signup link. It works once and expires after 48 hours.',
            'Choose a password of at least 12 characters, type it again under Confirm password, then select Create account.',
            'Sign in with your invited email address and the password you chose. Enter your authenticator code if you have enabled one.'
        ]),
        ('2. Sign in next time', [
            'Use the regular app address below or your CrownOps icon. You do not need the invitation link again.',
            'Forgot your password? Choose that option on the sign-in screen. If recovery is unavailable or the email does not arrive, contact the owner.'
        ]),
        ('3. Your first steps - '+ROLE_LABELS[role], ROLE_STEPS[role]),
        ('4. Add the CrownOps icon', [label+': '+instructions for label,instructions in DEVICE_STEPS]),
        ('5. Get help', [
            'Open Help & install inside CrownOps for device instructions. Use Important contacts for company service numbers.',
            'Check your name after opening the icon; sign in again if asked. Your role controls the screens you can use.',
            'Internet is required. Sign out on shared devices and keep your password and invitation link private.',
            'If your name, role or assigned jobs look wrong, contact the '+company_name+' owner before entering work.'
        ])
    ]
    intro = ('Hi '+name+',\n\nYou have been invited to CrownOps, the '+company_name+' team workspace.\n'
             'Your account email: '+email+'\nYour access: '+ROLE_LABELS[role])
    link = signup_url or '[Your private signup link will be added when the invitation is created.]'
    plain = intro+'\n\nCREATE YOUR ACCOUNT\n'+link+'\n\n'
    for title, items in sections:
        plain += title+'\n'+'\n'.join('- '+item for item in items)+'\n\n'
        if title.startswith('2.'):
            plain += 'Regular app address: '+address+'\n\n'
    plain += company_name+'\nIf you were not expecting this invitation, contact the '+company_name+' owner.'
    accent = '#c4510a'
    button = ('<a href="'+escape(signup_url,quote=True)+'" style="display:inline-block;padding:14px 22px;background:'+accent+';color:#ffffff;text-decoration:none;border-radius:8px;font-weight:bold">Create my CrownOps account</a>'
              if signup_url else '<p style="padding:14px;background:#fff2e9">Your private signup button will appear here.</p>')
    body = '<p style="margin:0;color:#626262;font-size:14px;font-weight:bold;letter-spacing:1px">'+escape(company_name.upper())+'</p><h1 style="font-size:28px;line-height:1.2;color:#161616">Welcome to CrownOps</h1>'
    body += '<p>Hi '+escape(name)+',</p><p>Your team account is ready to set up.</p><p><strong>Account email:</strong> '+escape(email)+'<br><strong>Access:</strong> '+escape(ROLE_LABELS[role])+'</p>'+button
    if signup_url:
        body += '<p style="font-size:14px;color:#626262;overflow-wrap:anywhere;word-break:break-all">If the button does not open, copy this private link:<br><a href="'+escape(signup_url,quote=True)+'">'+escape(signup_url)+'</a></p>'
    for title, items in sections:
        body += '<h2 style="font-size:18px;color:#161616;margin-top:26px">'+escape(title)+'</h2>'
        body += '<ul style="padding-left:22px">'+''.join('<li style="margin:0 0 10px">'+escape(item)+'</li>' for item in items)+'</ul>'
        if title.startswith('2.'):
            body += '<p><strong>Regular app address:</strong><br><a style="overflow-wrap:anywhere;word-break:break-all" href="'+escape(address,quote=True)+'">'+escape(address)+'</a></p>'
    body += '<p style="font-size:14px;color:#626262;border-top:1px solid #deddd9;padding-top:18px">'+escape(company_name)+'<br>If you were not expecting this invitation, contact the '+escape(company_name)+' owner.</p>'
    html = '<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Welcome to CrownOps</title></head><body style="margin:0;padding:16px;background:#f6f5f2;font:17px/1.6 Arial,sans-serif;color:#202020"><div style="max-width:620px;margin:0 auto;padding:24px;background:#ffffff;border:1px solid #deddd9;border-radius:12px;overflow-wrap:anywhere">'+body+'</div></body></html>'
    return {'subject':subject,'body':plain,'html':html}
