"""Professional evidence only: no location, citizenship, remote mode or salary input."""
import re


def geography_only_requirement(text):
    geographic = re.search(r'work authori[sz]|work permit|right to work|visa|citizenship|citizens?|residen|based in|located in|onsite|on-site|eu[- ]only|hiring (?:country|location)',text,re.I)
    professional = re.search(r'\b(experience|skills?|years?|testing|test|qa|playwright|selenium|sql|python|java|c\+\+|degree|certification|english|french|german|romanian|fluent|proficien\w*)\b',text,re.I)
    return bool(geographic and not professional)


def professional_assessment(job, candidate, assessment, config):
    weights = config['weights']
    total = sum(weights[key] for key in ('technical','experience','additional'))
    if total <= 0:
        raise ValueError('Professional criteria must have a positive total weight')
    score = round(sum(weights[key]*getattr(assessment,key) for key in ('technical','experience','additional')) / total * 100, 1)
    evidence = ' '.join([*candidate.skills,*candidate.projects,*candidate.positions]).casefold()
    specialized = [
        (r'\baxiomsl\b', r'\b(axiomsl|controllerview)\b', 'AxiomSL/ControllerView'),
        (r'\bcalypso\b', r'\bcalypso\b', 'Calypso'),
        (r'\bweb3\b', r'\b(web3|blockchain|smart contracts?)\b', 'Web3/blockchain'),
        (r'\bkernel\b', r'\b(kernel|device driver)\b', 'Linux kernel/device testing'),
        (r'\blinux\b.*\b(?:iot|pc)\b', r'\b(hardware|firmware|iot|embedded)\b', 'Linux hardware/IoT testing'),
    ]
    gaps = [label for title_pattern, evidence_pattern, label in specialized
            if re.search(title_pattern,job.title,re.I) and not re.search(evidence_pattern,evidence)]
    if gaps:
        return score, 'Not Recommended', 'Core specialization is not evidenced in the supplied profile: ' + ', '.join(gaps) + '. This is an evidence gap, not a claim of inability.'
    if re.search(r'\b(junior|intern|trainee)\b',job.title,re.I) and candidate.experience_years >= 5:
        return score, 'Needs Review', 'Advertised junior/intern level may not match the candidate seniority; review scope and expectations.'
    if assessment.uncertain_mandatory:
        return score, 'Needs Review', 'Professional evidence overlaps, but mandatory skills, experience or language requirements need verification.'
    fit = 'Strong Match' if score >= 80 else 'Good Match' if score >= 60 else 'Needs Review'
    reason = 'Professional score uses technical, experience and additional criteria only; geography and salary are assessed separately.'
    if fit == 'Needs Review':
        reason += ' Sparse professional evidence needs manual review; a low keyword score alone does not establish inability.'
    return score, fit, reason
