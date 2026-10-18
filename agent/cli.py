import argparse
import json
from agent.settings import ROOT


def verify_boards(registry_path=None, include_disabled=False):
    """Explicit network command. Access evidence does not establish employer ownership."""
    import httpx
    from agent.registry import load_boards
    from agent.collectors import collect, ENDPOINTS
    from agent.models import now
    from agent.logging_config import setup_logging
    setup_logging()
    boards, errors = load_boards(registry_path)
    results = []
    with httpx.Client(timeout=30,headers={'User-Agent':'QAJobAgent/0.1 (public API reader)'},follow_redirects=True) as client:
        for board in boards:
            if not board['enabled'] and not include_disabled:
                continue
            result = {'company':board['company'],'platform':board['platform'],'board':board['board'],
                      'endpoint':ENDPOINTS[board['platform']].format(board=board['board']),
                      'checked_at':now(),'official_careers_url':board.get('evidence_url'),'http_status':None,'publishes_jobs':None,'published_jobs':None,'qa_related_jobs':None,'ownership':'Requires manual confirmation on employer careers page'}
            try:
                jobs = collect(board,client)
                warnings = getattr(jobs,'warnings',[])
                from agent.matching import relevant
                from agent.settings import rules
                qa_jobs = [job for job in jobs if relevant(job,rules())]
                count = getattr(jobs,'published_count',len(jobs))
                result.update(status='Accessible with warnings' if warnings else 'Accessible',postings=len(jobs),warnings=warnings,
                              http_status=getattr(jobs,'http_status',None),publishes_jobs=count>0,published_jobs=count,
                              qa_related_jobs=[{'title':j.title,'location':j.location,'url':str(j.url)} for j in qa_jobs])
            except Exception as exc:
                result.update(status='Unavailable',error=type(exc).__name__,http_status=exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else None)
            results.append(result)
    report = {'results':results,'registry_errors':errors,'note':'Endpoint access alone does not verify employer identity or Moldova eligibility.'}
    (ROOT/'data').mkdir(exist_ok=True)
    (ROOT/'data/board_verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return 0 if results and not errors and all(r['status']=='Accessible' for r in results) else 1


def main():
    parser = argparse.ArgumentParser(description='Local QA job discovery; never submits applications')
    parser.add_argument('command',choices=['discover','verify-boards','verify-employer','doctor','extract-cv'])
    parser.add_argument('--no-ai',action='store_true')
    parser.add_argument('--pdf')
    parser.add_argument('--registry',help='Registry YAML to verify; discovery always uses the active registry')
    parser.add_argument('--include-disabled',action='store_true',help='Explicitly probe disabled research entries during verification only')
    parser.add_argument('--company',help='Employer name for official careers-link verification')
    parser.add_argument('--careers-url',help='Official HTTPS employer careers URL (not an aggregator)')
    args = parser.parse_args()
    if args.command == 'doctor':
        from agent.diagnostics import main as doctor
        raise SystemExit(doctor())
    if args.command == 'verify-boards':
        raise SystemExit(verify_boards(args.registry,args.include_disabled))
    if args.command == 'verify-employer':
        if not args.company or not args.careers_url or not args.careers_url.startswith('https://'):
            parser.error('--company and an official HTTPS --careers-url are required')
        import httpx
        from agent.source_verification import verify_employer
        with httpx.Client(timeout=30,follow_redirects=False,headers={'User-Agent':'QAJobAgent/0.1 (public API reader)'}) as client:
            result = verify_employer(args.company,args.careers_url,client)
        print(json.dumps(result,indent=2))
        raise SystemExit(0 if result and all(evidence['status']=='verified' for _,evidence in result) else 1)
    if args.command == 'extract-cv':
        if not args.pdf:
            parser.error('--pdf is required')
        from agent.settings import extract_pdf
        print(extract_pdf(args.pdf))
        return
    from filelock import Timeout
    from agent.discovery import discover
    try:
        result = discover(not args.no_ai)
        print(json.dumps(result,indent=2))
        if result['status']=='Failed':
            raise SystemExit(1)
        if result['status']=='Partial':
            raise SystemExit(3)
    except Timeout:
        print('Search already running.')
        raise SystemExit(2)


if __name__ == '__main__':
    main()
