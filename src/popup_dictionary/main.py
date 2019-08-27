# -*- coding: utf-8 -*-

# Popup Dictionary Add-on for Anki
#
# Copyright (C)  2018-2019 Aristotelis P. <https://glutanimate.com/>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version, with the additions
# listed at the end of the license file that accompanied this program.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# NOTE: This program is subject to certain additional terms pursuant to
# Section 7 of the GNU Affero General Public License.  You should have
# received a copy of these additional terms immediately following the
# terms and conditions of the GNU Affero General Public License that
# accompanied this program.
#
# If not, please request a copy through one of the means of contact
# listed here: <https://glutanimate.com/contact/>.
#
# Any modifications to this file must keep this entire header intact.

"""
Initializes add-on components.
"""

import re

import aqt
from aqt.qt import *
from aqt import mw
from aqt.reviewer import Reviewer
from aqt.utils import askUser
from anki.hooks import wrap, addHook
from anki.utils import json

from .web import html, initializeWeb
from .consts import *
from .config import CONFIG
from .template import addModel


# UI messages

WRN_RESCOUNT = ("<b>{}</b> relevant notes found.<br>"
                "The tooltip could take a lot of time to render and <br>"
                "temporarily slow down Anki.<br><br>"
                "<b>Are you sure you want to proceed?</b>")


# HTML format strings for results

pycmd = "pycmd"

html_reslist = """<div class="tt-reslist">{}</div>"""

html_res_normal = """\
<div class="tt-res" data-nid={{}}>{{}}<div title="Browse..." class="tt-brws"
onclick='{}("dctBrws:" + this.parentNode.dataset.nid)'>&rarr;</div></div>\
""".format(pycmd)

html_res_dict = """\
<div class="tt-res tt-dict" data-nid={{}}>
    <div class="tt-dict-title">Definition:</div>
    {{}}
    <div title="Browse..." class="tt-brws" onclick='{}("dctBrws:" + this.parentNode.dataset.nid)'>&rarr;</div>
</div>""".format(pycmd)

html_field = """<div class="tt-fld">{}</div>"""

# RegExes for cloze marker removal

cloze_re_str = r"\{\{c(\d+)::(.*?)(::(.*?))?\}\}"
cloze_re = re.compile(cloze_re_str)

# Functions that compose tooltip content

def getContentFor(term, ignore_nid):
    """Compose tooltip content for search term.
    Returns HTML string."""

    dict_entry = None
    note_content = None
    content = []

    if CONFIG["dictionaryEnabled"]:
        dict_entry = searchDefinitionFor(term)
        if dict_entry:
            content.append(dict_entry)

    if CONFIG["snippetsEnabled"]:
        note_content = getNoteSnippetsFor(term, ignore_nid)

        if note_content:
            content.extend(note_content)

    if content:
        return html_reslist.format("".join(content))
    elif note_content is False:
        return ""
    elif note_content is None:
        return "No other results found." if CONFIG["generalConfirmEmpty"] else ""


def getNoteSnippetsFor(term, ignore_nid):
    """Find relevant note snippets for search term.
    Returns list of HTML strings."""

    print("getNoteSnippetsFor called")
    # exclude current note
    current_nid = mw.reviewer.card.note().id
    exclusion_tokens = ["-nid:{}".format(current_nid)]

    if ignore_nid:
        exclusion_tokens.append("-nid:{}".format(ignore_nid))

    if CONFIG["snippetsLimitToCurrentDeck"]:
        exclusion_tokens.append("deck:current")

    # construct query string
    query = u'''"{}" {}'''.format(term, " ".join(exclusion_tokens))

    # NOTE: performing the SQL query directly might be faster
    res = sorted(mw.col.findNotes(query))
    print("getNoteSnippetsFor query finished.")

    if not res:
        return None

    # Prevent slowdowns when search term is too common
    res_len = len(res)
    warn_limit = CONFIG["snippetsResultsWarnLimit"]
    if warn_limit > 0 and res_len > warn_limit:
        if not askUser(WRN_RESCOUNT.format(res_len), title="Popup Dictionary"):
            return False

    note_content = []
    excluded_flds = CONFIG["snippetsExcludedFields"]
    for nid in res:
        note = mw.col.getNote(nid)
        valid_flds = [html_field.format(
            i[1]) for i in note.items() if i[0] not in excluded_flds]
        joined_flds = "".join(valid_flds)
        # remove cloze markers
        filtered_flds = cloze_re.sub(r"\2", joined_flds)
        note_content.append(html_res_normal.format(nid, filtered_flds))

    return note_content


def searchDefinitionFor(term):
    """Look up search term in dictionary deck.
    Returns HTML string."""
    query = u"""note:"{}" {}:"{}" """.format(CONFIG["dictionaryNoteTypeName"],
                                             CONFIG["dictionaryTermFieldName"],
                                             term)
    res = mw.col.findNotes(query)
    if res:
        nid = res[0]
        note = mw.col.getNote(nid)
        try:
            result = note[CONFIG["dictionaryDefinitionFieldName"]]
        except KeyError:
            return None
        return html_res_dict.format(nid, result)

    return None


def onReviewerHotkey():
    if mw.state != "review":
        return
    mw.reviewer.web.eval("invokeTooltipAtSelectedElm();")


def linkHandler(self, url, _old):
    """JS <-> Py bridge"""
    if url.startswith("dctBrws"):
        (cmd, arg) = url.split(":", 1)
        if not arg:
            return
        browseToNid(arg)
    elif url.startswith("dctLookup"):
        (cmd, payload) = url.split(":", 1)
        term, ignore_nid = json.loads(payload)
        term = term.strip()
        return getContentFor(term, ignore_nid)
    else:
        return _old(self, url)


def browseToNid(nid):
    """Open browser and find cards by nid"""
    browser = aqt.dialogs.open("Browser", mw)
    browser.form.searchEdit.lineEdit().setText("nid:'{}'".format(nid))
    browser.onSearchActivated()

def onRevHtml(self, _old):
    return _old(self) + html


def onProfileLoaded():
    """Delayed setup on profile init: Reviewer mods and model creation"""
    
    # Wrap here to try to counteract bad practices in other add-ons
    # (overwriting revHtml in its entirety)
    Reviewer.revHtml = wrap(Reviewer.revHtml, onRevHtml, "around")
    Reviewer._linkHandler = wrap(Reviewer._linkHandler, linkHandler, "around")

    if CONFIG["dictionaryEnabled"]:
        mid = mw.col.models.byName(CONFIG["dictionaryNoteTypeName"])
        if not mid:
            addModel(mw.col)
            mw.reset()


def initializeAddon():
    initializeWeb()
    
    # Menus and hotkeys
    QShortcut(QKeySequence(CONFIG["generalHotkey"]),
              mw, activated=onReviewerHotkey)

    # Hooks
    addHook("profileLoaded", onProfileLoaded)
