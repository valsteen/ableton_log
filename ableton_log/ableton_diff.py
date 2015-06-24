from collections import defaultdict
from decimal import Decimal
import itertools
from pprint import pprint
import gzip
import sys
import unittest
import logging
from lxml import etree


# noinspection PyProtectedMember
from lxml.etree import _Element as Element


IGNORED_TAGS = {'AnchorTime', 'GroupedTracksRevealed', 'IsContentSelected', 'NoteEditorFoldInZoom',
                'NoteEditorFoldInScroll', 'OtherTime', 'SelectedDevice', 'SelectedEnvelope', 'TrackUnfolded',
                'ClientSize', 'HighlightedTrackIndex', 'ViewStateDetailIsSample', 'CurrentTime',
                'LastSelectedTimeableIndex', 'GridIntervalPixel', 'CurrentZoom', 'ViewStateSessionMixerHeight'}


def get_contents(filename):
    return etree.parse(gzip.open(filename, 'rb')).getroot()


class GenericNode(object):
    def __init__(self, node):
        """

        :type node: Element
        """
        self.node = node


    def iter_children(self):
        """

        :rtype : collections.Iterable[GenericNode]
        """
        return itertools.ifilter(None, (node_factory(child_node) for child_node in self.node))

    def __repr__(self):
        return self.describe()

    def describe(self, tag_decoration=None):
        output = "<" + self.node.tag
        if tag_decoration:
            output += " " + tag_decoration

        attributes = " ".join('%s="%s"' % (k, v) for k, v in self.node.attrib.items())
        if attributes:
            output += " " + attributes
        if self.text:
            return output + ">" + self.text + "</" + self.node.tag + ">"
        else:
            return output + " />"

    @property
    def text(self):
        if not self.node.text is None:
            return self.node.text.strip(" \n\r\t") or None

    def shallow_equal(self, other):
        return \
            self.node.tag == other.node.tag and \
            self.node.attrib == other.node.attrib and \
            (self.text is None and other.text is None or self.text == other.text)

    def __eq__(self, other):
        return self is other


registry = defaultdict(lambda: GenericNode)


def register(klass):
    registry[klass.tag_name] = klass
    return klass


@register
class LeftTime(GenericNode):
    tag_name = "LeftTime"

    def shallow_equal(self, other):
        if self.node.tag == other.node.tag and (self.text is None and other.text is None or self.text == other.text):
            attributes = dict(self.node.attrib)
            other_attributes = dict(other.node.attrib)

            # value seem to move just a little at save, probably rounding errors, ignore them
            value = Decimal(attributes.pop("Value")).quantize(Decimal(".01"))
            other_value = Decimal(other_attributes.pop("Value")).quantize(Decimal(".01"))
            return value == other_value and attributes == other_attributes
        return False


@register
class RightTime(LeftTime):
    tag_name = "RightTime"


class GenericClip(GenericNode):
    def describe(self, tag_decoration=None):
        return super(GenericClip, self).describe("[%s]" % self.node.xpath("Name/@Value")[0])


@register
class MidiClip(GenericClip):
    tag_name = "MidiClip"


@register
class AudioClip(GenericClip):
    tag_name = "AudioClip"


class GenericTrack(GenericNode):
    def describe(self, tag_decoration=None):
        return super(GenericTrack, self).describe("[%s]" % self.node.xpath("Name/EffectiveName/@Value")[0])


@register
class MidiTrack(GenericTrack):
    tag_name = "MidiTrack"


@register
class GroupTrack(GenericTrack):
    tag_name = "GroupTrack"


@register
class KeyTrack(GenericNode):
    tag_name = "KeyTrack"

    def shallow_equal(self, other):
        midi_key_value = self.node.xpath("MidiKey/@Value")[0]
        other_midi_key_value = other.node.xpath("MidiKey/@Value")[0]
        return super(KeyTrack, self).shallow_equal(other) and midi_key_value == other_midi_key_value

    def describe(self, tag_decoration=None):
        return super(KeyTrack, self).describe("[MidiKey %s]" % self.node.xpath("MidiKey/@Value")[0])


@register
class Scene(GenericNode):
    tag_name = "Scene"

    def describe(self, tag_decoration=None):
        attributes = dict(self.node.attrib.items())
        name = attributes.pop("Value")

        output = "<Scene [%s]" % name.strip()

        attributes_str = " ".join('%s="%s"' % (k, v) for k, v in attributes)
        if attributes_str:
            output += " " + attributes_str

        return output + " />"


def node_factory(node):
    """

    :type node: Element
    """

    if not node.tag in IGNORED_TAGS:
        # noinspection PyCallingNonCallable
        return registry[node.tag](node)


def recurse_diff(oldnode, newnode):
    """

    :type oldnode: GenericNode
    :type newnode: GenericNode
    """

    changes = []

    old_children = oldnode.iter_children()
    new_children = newnode.iter_children()

    while True:
        try:
            old_child = next(old_children)
        except StopIteration:
            try:
                new_child = next(new_children)
            except StopIteration:
                break
            else:
                changes.append(('added', new_child))
        else:
            try:
                new_child = next(new_children)
            except StopIteration:
                changes.append(('removed', old_child))
            else:
                if not old_child.shallow_equal(new_child):
                    logging.debug("Not matching in new, looking forward for %s", old_child)

                    intermediary_changes = []
                    new_children, next_new_children = itertools.tee(new_children)
                    intermediary_changes.append(('added', new_child))
                    while True:
                        try:
                            next_new_child = next(next_new_children)
                        except StopIteration:
                            logging.debug("Not found at all in new, consider removed in old and step back in new %s",
                                          old_child)
                            changes.append(('removed', old_child))
                            new_children = itertools.chain([new_child], new_children)
                            break
                        else:
                            if old_child.shallow_equal(next_new_child):
                                changes.extend(intermediary_changes)
                                new_children = next_new_children
                                break
                            else:
                                # found it, skipped items have been added
                                intermediary_changes.append(('added', next_new_child))
                else:
                    changes.append(('unchanged', old_child, new_child))

    changes_result = []
    for change in changes:
        if change[0] in ('added', 'removed'):
            changes_result.append(change)
        else:
            child_changes = recurse_diff(change[1], change[2])
            if child_changes:
                changes_result.append(('changed', change[2], child_changes))

    return changes_result


class TestReleaseNotes(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        super(TestReleaseNotes, self).setUp()
        logging.basicConfig(level=logging.DEBUG)

    def expect_output(self, old, new, expected_result):
        self.assertMultiLineEqual(
            expected_result,
            str(recurse_diff(node_factory(etree.fromstring(old)),
                             node_factory(etree.fromstring(new))))
        )

    def test_equal(self):
        self.expect_output(
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">no change</level3>
                        <level3 order="3"></level3>
                        <level3 order="4"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">no change</level3>
                        <level3 order="3"></level3>
                        <level3 order="4"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            "[]"
        )

    def test_simple_string_change(self):
        self.expect_output(
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3 label="theonethatchanges" identity="1">
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2"></level3>
                        <level3 order="3">this text will change</level3>
                        <level3 order="4"></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3 label="theonethatchanges" identity="1">
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2"></level3>
                        <level3 order="3">this text has changed</level3>
                        <level3 order="4"></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """[('changed', <sub3 label="theonethatchanges" identity="1" />, [('changed', <nodethatchanges />, [('removed', <level3 order="3">this text will change</level3>), ('added', <level3 order="3">this text has changed</level3>)])])]"""
        )


    def test_remove_at_start(self):
        self.expect_output(
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2"></level3>
                        <level3 order="3"></level3>
                        <level3 order="4"></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="2"></level3>
                        <level3 order="3"></level3>
                        <level3 order="4"></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """[('changed', <sub3 />, [('changed', <nodethatchanges />, [('removed', <level3 order="1" />)])])]"""
        )

    def test_remove_at_end(self):
        self.expect_output(
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2"></level3>
                        <level3 order="3"></level3>
                        <level3 order="4"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2"></level3>
                        <level3 order="3"></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """[('changed', <sub3 />, [('changed', <nodethatchanges />, [('removed', <level3 order="4" />)])])]"""
        )

    def test_remove_middle(self):
        self.expect_output(
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">I will be removed!</level3>
                        <level3 order="3"></level3>
                        <level3 order="4"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="3"></level3>
                        <level3 order="4"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """[('changed', <sub3 />, [('changed', <nodethatchanges />, [('removed', <level3 order="2">I will be removed!</level3>)])])]"""
        )

    def test_add_at_start(self):
        self.expect_output(
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">I will be removed!</level3>
                        <level3 order="3"></level3>
                        <level3 order="4"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="0"><header></header><content>I have been added</content></level3>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">I will be removed!</level3>
                        <level3 order="3"></level3>
                        <level3 order="4"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """[('changed', <sub3 />, [('changed', <nodethatchanges />, [('added', <level3 order="0" />)])])]"""
        )

    def test_add_at_end(self):
        self.expect_output(
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">no change</level3>
                        <level3 order="3"></level3>
                        <level3 order="4"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">no change</level3>
                        <level3 order="3"></level3>
                        <level3 order="4"><header></header><content>dummy end</content></level3>
                        <level3 order="5"><header></header><content>I have been added!</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """[('changed', <sub3 />, [('changed', <nodethatchanges />, [('added', <level3 order="5" />)])])]"""
        )

    def test_add_middle(self):
        self.expect_output(
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">no change</level3>
                        <level3 order="3"></level3>
                        <level3 order="4"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">no change</level3>
                        <level3 order="2.5"><header></header><content>I have been added!</content></level3>
                        <level3 order="3"></level3>
                        <level3 order="4"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """[('changed', <sub3 />, [('changed', <nodethatchanges />, [('added', <level3 order="2.5" />)])])]"""
        )

    def test_change_at_start(self):
        self.expect_output(
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">no change</level3>
                        <level3 order="3"></level3>
                        <level3 order="4"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1" changed="1"><header></header><content>hi</content></level3>
                        <level3 order="2">no change</level3>
                        <level3 order="3"></level3>
                        <level3 order="4"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """[('changed', <sub3 />, [('changed', <nodethatchanges />, [('removed', <level3 order="1" />), ('added', <level3 order="1" changed="1" />)])])]"""
        )

    def test_change_at_end(self):
        self.expect_output(
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">no change</level3>
                        <level3 order="3"></level3>
                        <level3 order="4" changed="0"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">no change</level3>
                        <level3 order="3"></level3>
                        <level3 order="4" changed="1"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """[('changed', <sub3 />, [('changed', <nodethatchanges />, [('removed', <level3 order="4" changed="0" />), ('added', <level3 order="4" changed="1" />)])])]"""
        )

    def test_change_middle(self):
        self.expect_output(
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">no change</level3>
                        <level3 order="3" changed="false"><header></header><content>hi</content></level3>
                        <level3 order="4" changed="0"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """
            <root>
                <sub1>
                    <subsub1 dummy="1">text1</subsub1>
                    <subsub2 dummy="2">text2</subsub2>
                    <subsub3 dummy="3">text3</subsub3>
                </sub1>
                <sub2>
                    <subsub21 dummy="1">text1</subsub21>
                    <subsub22 dummy="2">text2</subsub22>
                    <subsub23 dummy="3">text3</subsub23>
                </sub2>
                <sub3>
                    <subsub31 dummy="1">text1</subsub31>
                    <subsub32 dummy="2">text2</subsub32>
                    <nodethatchanges>
                        <level3 order="1"><header></header><content>hi</content></level3>
                        <level3 order="2">no change</level3>
                        <level3 order="3" changed="true"></level3>
                        <level3 order="4" changed="0"><header></header><content>dummy end</content></level3>
                    </nodethatchanges>
                    <subsub33 dummy="3">text3</subsub33>
                </sub3>
                <sub4>
                    <subsub41 dummy="1">text1</subsub41>
                    <subsub42 dummy="2">text2</subsub42>
                    <subsub43 dummy="3">text3</subsub43>
                </sub4>
            </root>
            """,
            """[('changed', <sub3 />, [('changed', <nodethatchanges />, [('removed', <level3 order="3" changed="false" />), ('added', <level3 order="3" changed="true" />)])])]"""
        )


def run():
    if "-D" in sys.argv:
        logging.basicConfig(level=logging.DEBUG)
        sys.argv.remove("-D")

    if len(sys.argv) > 2:
        content1 = get_contents(sys.argv.pop())
        content2 = get_contents(sys.argv.pop())

        pprint(recurse_diff(node_factory(content1), node_factory(content2)))
