"""Search-geography decisions, independent of technical skill scores and salary."""
import re
import unicodedata
from agent.collectors import plain


def normalized(text):
    return ''.join(char for char in unicodedata.normalize('NFKD', text.casefold())
                   if not unicodedata.combining(char))


def contains(text, values):
    return any(re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)', text) for value in values)


CITY_COUNTRIES = {
    'Moldova': ['moldavia', 'chisinau', 'balti'],
    'Romania': ['bucharest', 'bucuresti', 'cluj', 'timisoara', 'iasi'],
    'France': ['paris', 'rennes'], 'Germany': ['berlin', 'munich'],
    'Netherlands': ['amsterdam'], 'Poland': ['warsaw', 'krakow'],
    'Czechia': ['prague','pilsen','plzen'], 'Cyprus': ['nicosia', 'limassol'],
    'Latvia': ['riga'], 'Lithuania': ['kaunas','vilnius'],
    'Azerbaijan': ['baku'], 'Malaysia': ['kuala lumpur'],
    'Taiwan': ['taipei'], 'China': ['beijing'],
    'Brazil': ['sao paulo'], 'United States': ['usa', 'us', 'u.s.', 'washington, dc', 'new york','draper, ut'],
    'United Kingdom': ['uk', 'u.k.', 'london'], 'Ukraine': ['kyiv', 'kiev'],
    'Canada': ['toronto'], 'India': ['bangalore', 'bengaluru'],
}
OTHER_COUNTRIES = ['Australia','Japan','Singapore','Mexico','Argentina','Georgia',
                   'Uzbekistan','Israel','Turkey','Serbia','South Africa','New Zealand',
                   'United Arab Emirates','Pakistan','Philippines','Indonesia','Thailand',
                   'Vietnam','South Korea','Egypt','Armenia','Kazakhstan','Hong Kong']


def countries_in_text(value, eu):
    text = normalized(value)
    names = list(dict.fromkeys(['Moldova', *eu, *CITY_COUNTRIES, *OTHER_COUNTRIES]))
    return {country for country in names
            if contains(text, [normalized(country), *CITY_COUNTRIES.get(country, [])])}


def advertised_countries(job, eu):
    return countries_in_text(job.location + ' ' + job.title,eu)


def eu_residence_requirement(description):
    return bool(re.search(r'\b(?:work|working|reside|residing|based|located) (?:from|in|within) (?:the )?(?:european union|eu|eea)\b[^.\n]{0,100}\b(required|mandatory|only)\b',description)
                or re.search(r'\b(?:eu|eea|european union)[- ]only\s+(?:hiring|candidates?|residents?|roles?)\b|\b(?:hiring|remote|candidates?)\s+(?:is\s+)?(?:eu|eea|european union)[- ]only\b',description))


def geographic_uncertainty(job, eu):
    if 'Moldova' in advertised_countries(job,eu) and eu_residence_requirement(normalized(plain(job.description))):
        return 'Advertised locations include Moldova/Moldavia, but the description requires EU residence/work permission; conflicting scope needs employer confirmation.'
    scope=normalized(job.location+' '+job.title)
    if contains(scope,['moldavia']) and not contains(scope,['moldova']):
        return 'ATS uses Moldavia; confirm that this means Moldova hiring rather than a regional label before treating access as verified.'
    return None


def excluded_geography(job, candidate, eu):
    """Return a definite exclusion, or None to keep uncertain access reviewable."""
    scope = normalized(job.location + ' ' + job.title)
    description = normalized(plain(job.description))
    countries = advertised_countries(job, eu)
    target = countries & {'Moldova', *eu}
    outside = countries - {'Moldova', *eu}
    regional_scope = bool(re.search(r'\b(eu|europe|european union|emea|worldwide)\b', scope)
                          or re.search(r'\b(global|anywhere)\b',normalized(job.location)))
    regional_target = bool(re.search(r'\b(eu|europe|european union|emea)\b',scope))
    broad_remote = regional_scope or bool(re.search(r'(?:remote|work).{0,20}(?:worldwide|from anywhere)',description))
    if 'moldova' in normalized(candidate.location):
        # Explicit hiring-residence restrictions are not the same as unknown work rights.
        restrictions = re.findall(r'(?:must (?:be |reside |live )?(?:based|located)?\s*(?:in|within)|candidates (?:must be based in|based in)|hiring (?:only in|limited to))\s+([^.;\n]{1,100})',description)
        for restriction in restrictions:
            allowed = {country for country in ['Moldova', *eu, *CITY_COUNTRIES, *OTHER_COUNTRIES]
                       if contains(restriction,[normalized(country),*CITY_COUNTRIES.get(country,[])])}
            if allowed and 'Moldova' not in allowed and 'Moldova' not in countries:
                return 'Explicit hiring-residence requirement excludes current Moldova residence; allowed scope: ' + ', '.join(sorted(allowed))
        if eu_residence_requirement(description) and not contains(description,['moldova','moldavia']) and 'Moldova' not in countries:
            return 'Explicit EU/EEA-only hiring excludes current Moldova residence; EU residence is not assumed.'
    if outside and not target and not regional_target and job.remote in ('Onsite','Hybrid'):
        return 'Onsite/hybrid work outside Moldova, Romania and the EU: ' + ', '.join(sorted(outside))
    if outside and not target and not broad_remote:
        return 'Advertised hiring geography is outside the target countries; no Moldova/EU remote scope stated: ' + ', '.join(sorted(outside))
    return None


def geographic_priority(job, eligibility, eu):
    countries = advertised_countries(job, eu)
    if job.remote == 'Remote' and eligibility == 'Eligible':
        return 0  # Confirmed Moldova/worldwide remote scope.
    scope = normalized(job.location+' '+job.title)
    if job.remote == 'Remote' and eligibility == 'Needs Review' and (
            'Moldova' in countries or re.search(r'\b(europe|eu|emea|worldwide|global)\b',scope)):
        return 1  # Plausible Moldova remote access, explicitly unconfirmed.
    if 'Moldova' in countries:
        return 2
    if 'Romania' in countries:
        return 3
    if countries & set(eu):
        return 4  # Includes Cyprus with all other EU members.
    return 5
