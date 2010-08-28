#!/usr/bin/env python
"""
Import tasks from Cultured Code's Things into Remember The Milk.

This script tags imported tasks with 'things2rtm' so you can easily find them
and delete them all if something goes wrong.

Edit the first few lines of this file for configuration, such as whether to
ignore certain Things tags, and whether to do a dry run.

Requires rtmapi, Mariano Draghi's Python library for accessing Remember The
Milk's API, here:

http://code.google.com/p/rtmapi/

Tested with r15 of the code, current from 2010-06-20
"""

from __future__ import print_function

# config
default_listname = 'Inbox'
dry_run = False
# some Things tags that we won't import into Remember The Milk
ignored_tags = ['High', 'Medium', 'Low']

import rtmapi
from xml.dom.minidom import parse, parseString
from xml.etree.ElementTree import tostring
from xml.sax.saxutils import escape
import os
import re
import time

#--- GET INFO FROM YOUR REMEMBER THE MILK ACCOUNT ---

print('Accessing your Remember The Milk account')
api_key = '0e674177969f17a9f35b2c036509ebd8'
secret = 'f607f8f5caa92b32'

rtm = rtmapi.RtmAPI(api_key, secret)
(token, frob) = rtm.get_token_part_one(perms='delete')
if not token:
    raw_input("Press ENTER after you authorized this program")
rtm.get_token_part_two((token, frob))

name2list_id = {}
rsp = rtm.lists.getList()
for lst in rsp.find('lists').findall('list'):
    # Ignore smart lists
    if not int(lst.attrib['smart']):
        name2list_id[lst.attrib['name']] = lst.attrib['id']

name2taskseries_id = {}
rsp = rtm.tasks.getList()

for lst in rsp.find('tasks').findall('list'):
    for taskseries in lst.findall('taskseries'):
        name2taskseries_id[taskseries.attrib['name']] = taskseries.attrib['id']

print('Remember The Milk has %d task series in %d lists' % (
    len(name2taskseries_id), len(name2list_id)
))

#--- PARSE THE THINGS DATABASE ---

now = time.localtime()
today = time.struct_time((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, now.tm_wday, now.tm_yday, now.tm_isdst))

things_unescape_content_pattern = re.compile(r'\\u((\d|[a-f]){2})00')
def things_unescape_content(content):
    """
    Content in Things' Database.xml is escaped in a very strange way,
    where '<' becomes \u3e00.
    """
    def replace(match):
        # From a match like \u3e00, get the '3e' part, convert to int (base 16),
        # then to char
        return unichr(int(match.group(1), 16))
        
    return things_unescape_content_pattern.sub(replace, content)

def things_parse_content(content):
    #try:
    xml = parseString(things_unescape_content(content.encode('ascii', 'replace')).replace('&', '&amp;'))
    note = xml.documentElement
    if note.hasChildNodes:
        return note.childNodes[0].nodeValue
    else:
        return ''
    #except Exception, e:
    #    print(u'Discarding content with %s:\n\n%s\n\n' % (
    #        e, content.encode('ascii', 'replace')
    #    ))

class DatabaseObject:
    """
    Represents a Things database object
    """
    
    def __init__(self, node):
        """
        @param node:    A DOM Node
        """
        self.listname = default_listname
        self.id = node.getAttribute('id')
        self.type = node.getAttribute('type')
        # Make it easy to find and delete them all after a run
        self.tagnames = ['things2rtm']
        self.children = []
        self.content = None
        self.deleted = False
        attr_names = ['datecompleted', 'title', 'datedue']
        relationship_names = ['children', 'tags']
        
        # Make default values
        for attr_name in attr_names:
            setattr(self, attr_name, None)
        for relationship_name in relationship_names:
            setattr(self, relationship_name, [])
        
        for child in node.childNodes:
            if child.nodeName == 'attribute' and child.getAttribute('name') == 'content':
                content = child.childNodes[0].nodeValue
                self.content = things_parse_content(content)
            elif child.nodeName == 'attribute':
                if child.hasChildNodes():
                    value = things_unescape_content(child.childNodes[0].nodeValue)
                    if child.getAttribute('name') == 'datedue':
                        # TODO:
                        # I can't figure out how to parse the dates stored in Database.xml,
                        # so default datedue to today
                        value = today
                    setattr(self, child.getAttribute('name'), value)
            elif child.nodeName == 'relationship':
                if child.hasAttribute('idrefs'):
                    attribute_name = child.getAttribute('name')
                    if not getattr(self, attribute_name, None):
                        setattr(self, attribute_name, [])
                    getattr(self, attribute_name).extend(child.getAttribute('idrefs').split())
    
    def valid(self):
        # We define a 'valid' object as having a title, except FOCUS objects are
        # always valid
        return self.type == 'FOCUS' or bool(getattr(self, 'title', None))
    
    def __repr__(self):
        rv = '%s %s' % (self.id, repr(self.title))
        if self.datecompleted:
            rv += ' (completed)'
        
        return rv

db_path = os.path.join(
    os.path.expanduser('~/Library/Application Support/Cultured Code/Things'),
    'Database.xml'
)

print('Opening %s' % db_path)

db_xml = parse(db_path)
database = db_xml.documentElement
assert database.nodeName == 'database', "Expected top XML node of %s to be named 'database'" % db_path

db_objects = []
id2db_object = {}
for node in database.childNodes:
    if node.nodeName == 'object' and node.getAttribute('type') in ('TODO', 'FOCUS', 'TAG'):
        db_object = DatabaseObject(node)
        db_objects.append(db_object)
        id2db_object[db_object.id] = db_object

# find which objects are in which lists
for db_object in db_objects:
    if db_object.children:
        # it's not a TODO, it's a project
        for child_id in db_object.children:
            child = id2db_object[child_id]
            child.listname = db_object.title

# assign tags to objects, ignoring some Things default tags that we don't want
# in Remember The Milk
for db_object in db_objects:
    if db_object.type == 'TODO':
        tagnames = []
        for tag_id in db_object.tags:
            tag_object = id2db_object.get(tag_id)
            if tag_object:
                tagname = tag_object.title
                if tagname not in ignored_tags:
                    tagnames.append(tagname)
            else:
                print('Missing tag id %s for TODO with id %s' % (
                    tag_id, db_object.id
                ))
            
            db_object.tagnames.extend(tagnames)

# find deleted objects
trash = [dbo for dbo in db_objects if dbo.type == 'FOCUS' and getattr(dbo, 'identifier', None) == 'FocusTrash'][0]
for trashed_id in trash.focustodos:
    trashed = id2db_object[trashed_id]
    trashed.deleted = True

print('Database.xml has %d TODOs, %d valid, %d deleted, in %d lists' % (
    len([db_object for db_object in db_objects if db_object.type == 'TODO' and not db_object.children]),
    len([db_object for db_object in db_objects if db_object.type == 'TODO' and not db_object.children and db_object.valid()]),
    len([db_object for db_object in db_objects if db_object.type == 'TODO' and db_object.deleted]),
    len([db_object for db_object in db_objects if db_object.type == 'TODO' and db_object.children and db_object.valid()]),
))

print('Invalid tasks in Database.xml:')
for db_object in db_objects:
    if not db_object.valid():
        print(repr(db_object))

print()

#--- ADD TASKS FROM THINGS TO REMEMBER THE MILK ---

rsp = rtm.timelines.create()
timeline = rsp.find('timeline').text

for db_object in db_objects:
    if db_object.type == 'TODO' and db_object.valid() and not db_object.deleted:
        if db_object.title in name2taskseries_id:
            print("Skipping task %s: Already in Remember the Milk" % (
                repr(db_object.title)
            ))
        else:
            # Task not yet in Remember The Milk - is its list?
            if db_object.listname not in name2list_id:
                print('Creating list %s' % repr(db_object.listname))
                rsp = rtm.lists.add(
                    timeline=timeline,
                    name=db_object.listname,
                )
                
                assert rsp.attrib['stat'] == 'ok', "Failed to add list %s, id %s" % (
                    repr(db_object.title), repr(db_object.id)
                )
                
                list_id = rsp.find('list').attrib['id']
                name2list_id[db_object.listname] = list_id
            else:
                list_id = name2list_id[db_object.listname]
            
            print('Adding task %s to Remember The Milk' % (
                repr(db_object.title)
            ))
            
            # Create the Things task in Remember The Milk
            rsp = rtm.tasks.add(
                timeline=timeline,
                list_id=list_id,
                name=db_object.title,
            )
            
            # Get the created task's ids from Remember The Milk
            transaction_id = rsp.find('transaction').attrib['id']
            lst = rsp.find('list')
            taskseries = lst.find('taskseries')
            taskseries_id = taskseries.attrib['id']
            task_id = taskseries.find('task').attrib['id']
            
            # Add additional data from Things to the Remember The Milk task
            if db_object.tagnames:
                rtm.tasks.addTags(
                    timeline=timeline,
                    list_id=list_id,
                    taskseries_id=taskseries_id,
                    task_id=task_id,
                    tags = ','.join(db_object.tagnames),
                )
            
            if db_object.content:
                rtm.tasks.notes.add(
                    timeline=timeline,
                    list_id=list_id,
                    taskseries_id=taskseries_id,
                    task_id=task_id,
                    note_title="Note from Things",
                    note_text=db_object.content,
                )
            
            if db_object.datedue:
                rtm.tasks.setDueDate(
                    timeline=timeline,
                    list_id=list_id,
                    taskseries_id=taskseries_id,
                    task_id=task_id,
                    due=time.strftime('%Y-%m-%d', db_object.datedue),
                    has_due_time=False,
                    parse=False,
                )
            
            if db_object.datecompleted:
                # Can't set the date completed with the RTM API, so all tasks
                # will seem to have been completed just now
                rsp = rtm.tasks.complete(
                    timeline=timeline,
                    list_id=list_id,
                    taskseries_id=taskseries_id,
                    task_id=task_id,
                )
            
            if dry_run:
                # Delete the task we just added -- you can set a breakpoint
                # here to examine the task on the web before it's deleted
                rsp = rtm.tasks.delete(
                    timeline=timeline,
                    list_id=list_id,
                    taskseries_id=taskseries_id,
                    task_id=task_id,
                )

print('Done')
