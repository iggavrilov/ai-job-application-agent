import json
import pytest
from agent.models import Job, Match
from agent.matching import evaluate, geography, ranking_key, EU, region, relevant
from agent.settings import rules


def qa(location='Remote - Moldova', remote='Remote', title='Senior QA Engineer', description=None):
    return Job(title=title,company='Synthetic',location=location,remote=remote,
               description=description or 'Playwright JavaScript API testing SQL Drupal Regression testing',
               url='https://example.org/qa',platform='lever',source_id='one')


def test_professional_axis_is_independent_of_geography(candidate):
    local=evaluate(qa(),candidate,rules(),False)
    foreign=evaluate(qa('Baku','Onsite'),candidate,rules(),False)
    assert local.professional_score==foreign.professional_score
    assert local.professional_fit==foreign.professional_fit=='Strong Match'
    assert local.in_main_recommendations
    assert not foreign.in_main_recommendations
    assert foreign.eligibility=='Ineligible' and 'Onsite/hybrid' in foreign.geographic_reason
    assert foreign.exclusion_reasons and foreign.recommendation=='Not Recommended'


@pytest.mark.parametrize('location,mode',[
    ('Baku','Onsite'),('Kuala Lumpur','Onsite'),('Taipei','Onsite'),('Beijing','Hybrid'),
    ('Brazil','Remote'),('India','Remote'),('Washington, DC, USA','Onsite'),
    ('Baku - Worldwide team','Onsite'),
])
def test_definite_non_target_geography_is_excluded(candidate,location,mode):
    result=evaluate(qa(location,mode),candidate,rules(),False)
    assert result.eligibility=='Ineligible'
    assert not result.in_main_recommendations and result.exclusion_reasons


@pytest.mark.parametrize('location,mode',[
    ('Romania','Remote'),('Bucharest','Onsite'),('France','Hybrid'),('Cyprus','Remote'),
    ('Europe','Remote'),('EMEA','Remote'),('unknown','Unknown'),
])
def test_unknown_moldova_access_and_target_relocation_stay_reviewable(candidate,location,mode):
    result=evaluate(qa(location,mode),candidate,rules(),False)
    assert result.eligibility=='Needs Review'
    assert result.in_main_recommendations and result.recommendation=='Needs Review'
    assert not result.exclusion_reasons


def test_anywhere_in_europe_with_ukrainian_ats_location_is_not_dropped(candidate):
    result=evaluate(qa('Ukraine',title='QA Engineer (Anywhere in Europe)'),candidate,rules(),False)
    assert result.eligibility=='Needs Review' and result.in_main_recommendations


@pytest.mark.parametrize('requirement',[
    'You must be based in Germany.',
    'Work from the European Union region and a work permit are required.',
    'This is an EU-only hiring role.',
])
def test_explicit_residence_restriction_cannot_be_overridden_by_skills(candidate,requirement):
    job=qa('Europe',description=qa().description+'; '+requirement)
    result=evaluate(job,candidate,rules(),False)
    assert result.eligibility=='Ineligible' and not result.in_main_recommendations
    assert result.professional_score is not None


def test_core_professional_gap_is_separate_from_target_geography(candidate):
    result=evaluate(qa('Romania',title='QA Engineer (AxiomSL)'),candidate,rules(),False)
    assert result.eligibility=='Needs Review'
    assert result.professional_fit=='Not Recommended' and not result.in_main_recommendations
    assert 'specialization' in result.professional_reason
    qualified=candidate.model_copy(update={'skills':[*candidate.skills,'AxiomSL']})
    supported=evaluate(qa('Romania',title='QA Engineer (AxiomSL)'),qualified,rules(),False)
    assert supported.professional_fit=='Strong Match' and supported.in_main_recommendations


def test_sparse_professional_evidence_is_reviewable_not_fabricated(candidate):
    result=evaluate(qa('Europe',description='Testing a web application.'),candidate,rules(),False)
    assert result.professional_fit=='Needs Review' and result.in_main_recommendations


def test_geographic_priority_moldova_romania_eu_and_no_cyprus_category(candidate):
    local=evaluate(qa(),candidate,rules(),False)
    romania=evaluate(qa('Bucharest'),candidate,rules(),False)
    france=evaluate(qa('France'),candidate,rules(),False)
    cyprus=evaluate(qa('Cyprus'),candidate,rules(),False)
    assert local.geographic_priority < romania.geographic_priority < france.geographic_priority
    assert cyprus.geographic_priority==france.geographic_priority
    uncertain_remote=evaluate(qa('Ukraine',title='QA Engineer (Anywhere in Europe)'),candidate,rules(),False)
    assert local.geographic_priority < uncertain_remote.geographic_priority < romania.geographic_priority
    assert region('Cyprus')==region('France') and 'Cyprus' in EU
    def record(result):return {'Region':'Other / unknown','Remote status':'Remote','Professional score':result.professional_score,'Geographic priority':result.geographic_priority}
    assert ranking_key(record(local),'Geographic priority') < ranking_key(record(romania),'Geographic priority')


def test_salary_does_not_change_either_axis(candidate):
    job=qa('Europe')
    original=evaluate(job,candidate,rules(),False)
    changed=evaluate(job.model_copy(update={'salary':'1 EUR/year'}),candidate,rules(),False)
    assert original.professional_score==changed.professional_score
    assert original.eligibility==changed.eligibility
    assert original.in_main_recommendations==changed.in_main_recommendations


def test_legacy_match_schema_is_readable_but_not_implicitly_recommended(candidate):
    result=evaluate(qa(),candidate,rules(),False).model_dump()
    for key in ('professional_score','professional_fit','professional_reason','geographic_reason','geographic_priority','in_main_recommendations','exclusion_reasons'):
        result.pop(key)
    legacy=Match.model_validate_json(json.dumps(result))
    assert legacy.professional_score is None and not legacy.in_main_recommendations


def test_observed_qa_manual_title_variant():
    assert relevant(qa('Bulgaria',title='Senior QA Manual, Remote'),rules())


def test_moldavia_alias_and_conflicting_eu_residence_clause_need_review(candidate):
    job=qa('Bulgaria; Moldavia; Poland; Romania',description='Playwright SQL. Work from the European Union region and a work permit are required.')
    result=evaluate(job,candidate,rules(),False)
    assert result.eligibility=='Needs Review' and result.in_main_recommendations
    assert result.geographic_priority==1 and region(job.location)=='Moldova'


def test_regional_eu_cities_and_explicit_us_city_are_classified(candidate):
    assert evaluate(qa('Riga'),candidate,rules(),False).geographic_priority==4
    assert evaluate(qa('Pilsen'),candidate,rules(),False).eligibility=='Needs Review'
    assert evaluate(qa('Draper, UT'),candidate,rules(),False).eligibility=='Ineligible'


def test_product_marketing_is_not_a_hiring_residence_requirement(candidate):
    result=evaluate(qa('Europe',description='Test an EU-only payments product with Playwright.'),candidate,rules(),False)
    assert result.eligibility=='Needs Review' and result.in_main_recommendations


def test_global_job_title_does_not_override_specific_us_hiring_location(candidate):
    result=evaluate(qa('United States',title='Global QA Engineer'),candidate,rules(),False)
    assert result.eligibility=='Ineligible' and not result.in_main_recommendations


@pytest.mark.parametrize('clause',['Work authorization required.','You must be based in Germany.'])
def test_geographic_only_requirements_do_not_lower_professional_fit(candidate,clause):
    original=evaluate(qa(),candidate,rules(),False)
    changed=evaluate(qa(description=qa().description+'; '+clause),candidate,rules(),False)
    assert original.professional_score==changed.professional_score
    assert original.professional_fit==changed.professional_fit=='Strong Match'
    assert original.eligibility!=changed.eligibility


def test_moldavia_label_alone_is_not_verified_moldova_access(candidate):
    result=evaluate(qa('Moldavia'),candidate,rules(),False)
    assert result.eligibility=='Needs Review' and result.in_main_recommendations
