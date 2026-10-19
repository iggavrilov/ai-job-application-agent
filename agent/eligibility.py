"""Evidence-based work-location eligibility, separate from professional scoring."""
from __future__ import annotations

import re
import unicodedata

from agent.models import GeographicAssessment
from agent.collectors import plain


def _normal(text: str) -> str:
    value = unicodedata.normalize('NFKD', str(text or '').casefold())
    return ''.join(char for char in value if not unicodedata.combining(char))


def _has(text: str, phrase: str) -> bool:
    return bool(re.search(r'(?<![a-z])' + re.escape(_normal(phrase)) + r'(?![a-z])', text))


def assess_geographic_eligibility(job, candidate) -> GeographicAssessment:
    """Classify only what source evidence supports; preserve unresolved conditions."""
    from agent.matching import EU

    location = _normal(job.location)
    description = _normal(plain(job.description) + ' ' + plain(job.requirements))
    combined = location + ' ' + description
    countries = list(EU) + ['Moldova', 'United States']
    cities = {
        'Moldova': ['chisinau', 'balti'], 'Romania': ['bucharest', 'bucuresti', 'cluj', 'timisoara', 'iasi'],
        'Austria': ['vienna'], 'Belgium': ['brussels'], 'Bulgaria': ['sofia'], 'Croatia': ['zagreb'],
        'Cyprus': ['nicosia', 'limassol'], 'Czechia': ['prague'], 'Denmark': ['copenhagen'],
        'Estonia': ['tallinn'], 'Finland': ['helsinki'], 'France': ['paris'], 'Germany': ['berlin', 'munich'],
        'Greece': ['athens'], 'Hungary': ['budapest'], 'Ireland': ['dublin'], 'Italy': ['rome', 'milan'],
        'Latvia': ['riga'], 'Lithuania': ['vilnius'], 'Luxembourg': ['luxembourg city'],
        'Malta': ['valletta'], 'Netherlands': ['amsterdam'], 'Poland': ['warsaw', 'krakow'],
        'Portugal': ['lisbon'], 'Slovakia': ['bratislava'], 'Slovenia': ['ljubljana'],
        'Spain': ['madrid', 'barcelona'], 'Sweden': ['stockholm'], 'United States': ['washington dc', 'new york'],
    }
    found = {country for country in countries if _has(location, country)}
    for country, aliases in cities.items():
        if any(_has(location, alias) for alias in aliases):
            found.add(country)

    permitted = list(dict.fromkeys(job.remote_permitted_locations or []))
    if not permitted:
        # Only extract countries from clauses that explicitly describe remote scope.
        for match in re.finditer(r'(?:remote|work|working)\s+(?:from|in|within|across|throughout)\s+([^.;\n]{1,110})', description):
            clause = match.group(1)
            if re.search(r'\b(?:anywhere|worldwide|globally)\b', clause):
                permitted.append('Worldwide')
            permitted.extend(country for country in countries if _has(clause, country))
        if re.search(r'\b(?:remote worldwide|work from anywhere|worldwide remote|anywhere in the world)\b', combined):
            permitted.append('Worldwide')
        if re.search(r'\bremote\s+(?:eu|europe|european union)\b', combined):
            permitted.append('EU')
        if re.search(r'\bremote\s+emea\b', combined):
            permitted.append('EMEA')
    permitted = list(dict.fromkeys(permitted))

    # Narrow, explicit residency clauses are collected independently of workplace.
    required_residence = set()
    for match in re.finditer(r'(?:must be based in|must reside in|reside in|residence in|based in|located in)\s+([^.;\n]{1,100})', description):
        clause = match.group(1)
        required_residence.update(country for country in countries if _has(clause, country))

    citizen_facts = _normal(candidate.citizenship + ' ' + candidate.work_authorization)
    eu_authorized = bool(re.search(r'\b(?:romanian|eu) citizen\b|\bpassport holder\b', citizen_facts)
                         and re.search(r'\b(?:all eu member states|eu member states|european union|work in the eu)\b', citizen_facts))
    us_authorized = bool(re.search(r'\b(?:us|u\.s\.) citizen\b|\bauthorized to work in (?:the )?united states\b', citizen_facts))
    eu_auth = 'Confirmed for all EU member states' if eu_authorized else 'Needs Review'
    us_auth = 'Confirmed' if us_authorized else 'Not established; EU citizenship does not confer US work authorization'

    explicit_us_personal = bool(re.search(r'(?:must|required|only).{0,65}(?:us citizen|u\.s\. citizen|security clearance|work authorization)', description)
                               or re.search(r'(?:us citizen|u\.s\. citizen|us work authorization|work authorization in the united states).{0,65}(?:required|mandatory|only)', description))
    sponsor_no = bool(re.search(r'\b(?:no visa sponsorship|does not sponsor|cannot sponsor|without sponsorship|sponsorship unavailable)\b', description))
    sponsor_yes = bool(re.search(r'\b(?:visa sponsorship available|will sponsor|sponsorship provided|sponsor visas|visa sponsorship)\b', description)) and not sponsor_no
    visa = 'No' if sponsor_no else 'Yes—explicitly stated' if sponsor_yes else 'Not stated'
    contractor_terms = bool(re.search(r'\b(?:contractor|contracting|independent contractor|b2b|freelance)\b', combined))
    international_contract = bool(re.search(r'(?:international|worldwide|any country|global).{0,60}(?:contractor|contracting|b2b)|(?:contractor|contracting|b2b).{0,60}(?:international|worldwide|any country|global)', combined))
    contractor = ('International contractor explicitly accepted' if international_contract else
                  'Contract option mentioned; candidate location acceptance unverified' if contractor_terms else 'Not stated')
    eor = ('Explicitly available' if re.search(r'\b(?:employer of record|\beor\b)\b.{0,60}(?:available|provided|supported)|(?:available|provided|supported).{0,60}\b(?:employer of record|\beor\b)', combined)
           else 'Not stated')
    relocation_explicit = bool(re.search(r'\brelocation (?:is )?(?:required|mandatory|provided|supported|offered)\b|\bmust relocate\b', combined))
    remote = job.remote == 'Remote' or bool(re.search(r'\bremote\b', location))
    candidate_in_moldova = 'moldova' in _normal(candidate.location) or 'moldavia' in _normal(candidate.location)
    moldova_named = 'Moldova' in found or any(_has(_normal(item), 'Moldova') for item in permitted)
    worldwide = any(_has(_normal(item), 'worldwide') for item in permitted) or 'Worldwide' in permitted
    explicit_moldova_remote = remote and moldova_named
    target_eu = bool(found & set(EU)) or 'EU' in permitted or 'Europe' in permitted
    target_ro = 'Romania' in found or any(_has(_normal(item), 'Romania') for item in permitted)
    target_us = 'United States' in found or any(_has(_normal(item), 'United States') for item in permitted)
    evidence = []
    if job.location and job.location.casefold() != 'unknown':
        evidence.append('Source location: ' + job.location)
    if job.remote != 'Unknown':
        evidence.append('Source marks work mode as ' + job.remote)
    if permitted:
        evidence.append('Remote scope explicitly listed: ' + ', '.join(permitted))
    if required_residence:
        evidence.append('Residence wording names: ' + ', '.join(sorted(required_residence)))
    if sponsor_yes:
        evidence.append('Description explicitly states visa sponsorship is available')
    if international_contract:
        evidence.append('Description explicitly states international contractor eligibility')

    classification = 'Potentially Eligible — Needs Review'
    relocation = 'Unknown'
    confidence = 'Medium' if evidence else 'Low'
    if explicit_us_personal and not us_authorized and not sponsor_yes:
        classification = 'Confirmed Ineligible'
        us_auth = 'Not eligible under explicit US citizenship/work-authorization requirement'
        evidence.append('Explicit US-only citizenship/work-authorization requirement; no sponsorship stated')
        confidence = 'High'
    elif target_us and not us_authorized:
        if sponsor_yes:
            classification = 'Eligible If Relocating'
            relocation = 'US work location; employer states sponsorship is available, but candidate willingness to relocate is unconfirmed'
        elif international_contract and candidate_in_moldova:
            # The listing must explicitly accept international contractors; location-specific tax/engagement terms remain reviewable.
            classification = 'Potentially Eligible — Needs Review'
            contractor = 'International contractor explicitly accepted; Moldova engagement terms need confirmation'
            evidence.append('Confirm whether Moldova-based contractor engagement is supported')
        else:
            classification = 'Potentially Eligible — Needs Review'
            evidence.append('US work authorization or international contractor eligibility is not established')
    elif target_eu and eu_authorized:
        eu_auth = 'Confirmed for all EU member states'
        if remote and explicit_moldova_remote:
            classification = 'Potentially Eligible — Needs Review'
            evidence.append('Moldova remote location is named, but cross-border employer/payroll arrangement still needs confirmation')
        elif remote and (worldwide or 'EMEA' in permitted):
            classification = 'Potentially Eligible — Needs Review'
            evidence.append('Worldwide/EMEA wording does not by itself verify Moldova-based employment')
        else:
            classification = 'Eligible If Relocating'
            relocation = 'EU role; EU work authorization is confirmed, but candidate residence is Moldova and relocation willingness is not confirmed'
        if relocation_explicit:
            relocation = 'Employer explicitly requires/offers relocation; candidate decision remains unconfirmed'
        confidence = 'High' if found & set(EU) and eu_authorized else 'Medium'
    elif 'Moldova' in found or candidate_in_moldova:
        if explicit_moldova_remote:
            classification = 'Potentially Eligible — Needs Review'
            evidence.append('Moldova work location is in scope; Moldova employment/contractor authorization still requires confirmation')
        else:
            classification = 'Potentially Eligible — Needs Review'
            evidence.append('Current Moldova residence does not establish local work authorization or employer hiring arrangement')
    elif worldwide and international_contract:
        classification = 'Potentially Eligible — Needs Review'
        evidence.append('Worldwide contractor language is present; confirm Moldova-specific engagement terms')
    elif found and not (found & set(EU)) and 'United States' not in found:
        evidence.append('Advertised location is outside the requested EU, Moldova and US scope; no international contractor basis verified')
        if job.remote in {'Onsite', 'Hybrid'}:
            classification = 'Confirmed Ineligible'
            evidence.append('The role requires onsite or hybrid attendance outside the target geography')
            confidence = 'High'

    if required_residence and candidate_in_moldova and 'Moldova' not in required_residence:
        if required_residence & set(EU) and eu_authorized:
            classification = 'Eligible If Relocating'
            relocation = 'Employer requires residence in ' + ', '.join(sorted(required_residence)) + '; candidate currently lives in Moldova and has not confirmed relocation'
        elif 'United States' in required_residence and not us_authorized and not sponsor_yes:
            classification = 'Confirmed Ineligible'
            us_auth = 'Not established; required US residence/work authorization is unmet'
        elif required_residence:
            classification = 'Potentially Eligible — Needs Review'
            evidence.append('Required residence differs from current Moldova residence')

    if 'Moldova' in found and re.search(r'\b(?:eu[- ]only|work (?:from|in|within) the eu|eu member states)\b', description):
        evidence.append('The posting names Moldova while also requiring EU-only work/location eligibility; confirm whether the Moldova location is a separate option or excluded')
    if not evidence:
        evidence.append('No specific remote-permitted countries, residence rule, or work-authorization statement was found')

    if explicit_moldova_remote:
        priority = 0
    elif target_ro:
        priority = 1
    elif 'Moldova' in found:
        priority = 2
    elif remote and target_eu:
        priority = 3
    elif target_eu:
        priority = 4
    elif target_us and international_contract:
        priority = 5
    elif target_us and sponsor_yes:
        priority = 6
    elif worldwide:
        priority = 7
    else:
        priority = 8

    return GeographicAssessment(
        classification=classification,
        eu_work_authorization=eu_auth,
        us_work_authorization=us_auth,
        required_residence_countries=sorted(required_residence),
        remote_permitted_locations=permitted,
        relocation_requirement=relocation,
        visa_sponsorship=visa,
        contractor_eligibility=contractor,
        employer_of_record_availability=eor,
        confidence=confidence,
        evidence=evidence,
        priority=priority,
    )
