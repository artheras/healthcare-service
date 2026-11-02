"""Interactive local review; operator identity is asserted, not authenticated."""
import argparse
import sys
from .pubmed import PubMedClient, canonical
from .service import ResearchService
from .tasks import TaskService


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', required=True)
    p.add_argument('--task-id', required=True)
    p.add_argument('--expected-digest', required=True)
    p.add_argument('--reviewer', required=True)
    p.add_argument('--decision', choices=['approve', 'reject'], required=True)
    p.add_argument('--note', required=True)
    a = p.parse_args()
    if not sys.stdin.isatty():
        p.error('Review requires an interactive local terminal')
    svc = ResearchService(a.workspace, PubMedClient())
    try:
        tasks = TaskService(svc)
        task = tasks.get(a.task_id)
        print(canonical(task))
        if input('Type the complete expected digest to record this review: ').strip() != a.expected_digest:
            p.error('Digest confirmation did not match')
        print(canonical(tasks.record_operator_review(a.task_id, a.expected_digest, a.reviewer, a.decision, a.note)))
    finally:
        svc.close()


if __name__ == '__main__':
    main()
