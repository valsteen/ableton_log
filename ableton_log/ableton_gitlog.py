import gzip
from pprint import pprint
import zlib
from ableton_log.ableton_diff import recurse_diff, node_factory
from lxml import etree
import git
import sys
import time


def run():
    gitcmd = git.Git()
    gitlog = gitcmd.log("--format=%H", "--name-only", "--follow", "--", sys.argv[1]).split("\n")
    refs_names = zip(gitlog[0::3], gitlog[2::3])

    repo = git.Repo()

    def get_contents(ref, filename):
        blob = repo.commit(ref).tree[filename]
        return etree.fromstring(zlib.decompress(blob.data_stream.read(), 16 + zlib.MAX_WBITS))

    ref_names_iterator = iter(refs_names)

    ref, name = next(ref_names_iterator)
    new_content = get_contents(ref, name)

    for old_ref, old_name in ref_names_iterator:
        old_content = get_contents(old_ref, old_name)

        print gitcmd.log("--max-count=1", old_ref)
        print

        pprint(recurse_diff(node_factory(old_content), node_factory(new_content)))
        new_content = old_content