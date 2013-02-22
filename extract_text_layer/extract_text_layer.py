#!/usr/bin/python
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program; if not, write to the Free Software
#    Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#
#
#
#    copyright phe at some dot where

__module_name__ = "extract_text_layer_daemon"
__module_version__ = "1.0"
__module_description__ = "extract text layer daemon"

import match_and_split_config as config

import os
import socket
import re
import thread
import time
import copy

import align
import json
import wikipedia, pywikibot
import common_html

mylock = thread.allocate_lock()

# FIXME: try to avoid hard-coding this, pywikipedia know them but often
# pywikipedia code lag a bit behind new namespace creation, get it directly
# from the database (is it possible?) or through the api (but does all
# wikisource have a correct alias for the Page: namespace?)
page_prefixes = {
    'br' : 'Pajenn',
    'ca' : 'P\xc3\xa0gina',
    'de' : 'Seite',
    'en' : 'Page',
    'es' : 'P\xc3\xa1gina',
    'et' : 'Lehek\xc3\xbclg',
    'fr' : 'Page',
    'hr' : 'Stranica',
    'hu' : 'Oldal',
    'hy' : '\xd4\xb7\xd5\xbb',
    'it' : 'Pagina',
    'la' : 'Pagina',
    'no' : 'Side',
    'old': 'Page',
    'pl' : 'Strona',
    'pt' : 'P\xc3\xa1gina',
    'ru' : '\xd1\x81\xd1\x82\xd1\x80\xd0\xb0\xd0\xbd\xd0\xb8\xd1\x86\xd0\xb0',
    'sl' : 'Stran',
    'sv' : 'Sida',
    'vec': 'Pagina',
    'vi' : 'Trang',
    'zh' : 'Page',
    }

E_ERROR = 1
E_OK = 0

# FIXME: use a real Queue object and avoid polling the queue

# Get a job w/o removing it from the queue. FIXME: probably not the best
# way, if a job can't be handled due to exception and the exception is
# gracefully catched by the worker thread, there is no warranty than the
# job will be removed from the queue, so the worker thread can hang forever
# trying to do something causing an exception. This is not possible actually
# but it's fragile to not remove the job when getting it.
def get_job(lock, queue):
    got_it = False
    while not got_it:
        time.sleep(0.5)
        lock.acquire()
        if queue != []:
            title, codelang, user, t, conn = queue[-1]
            got_it = True
        lock.release()

    return title, codelang, user, t, conn

def remove_job(lock, queue):
    lock.acquire()
    queue.pop()
    lock.release()

def add_job(lock, queue, cmd):
    lock.acquire()
    queue.insert(0, cmd)
    lock.release()

def ret_val(error, text):
    if error:
        print "Error: %d, %s" % (error, text)
    return  { 'error' : error, 'text' : text }

def do_extract(mysite, maintitle, user, codelang):
    prefix = page_prefixes.get(codelang)
    if not prefix:
        return ret_val(E_ERROR, "no prefix")

    djvuname = maintitle.replace(u' ', u'_')
    print djvuname.encode('utf-8')

    filename = align.get_djvu(mysite, djvuname, True)
    if not filename:
        return ret_val(E_ERROR, "unable to retrieve djvu file")

    text = u''
    for i in range(align.get_nr_djvu_pages(filename)):
        text += u'==[[' + prefix + u':' + maintitle + u'/' + unicode(i+1) + u']]==\n'
        text += align.read_djvu_page(filename, i+1) + u'\n'

    page = wikipedia.Page(site = mysite, title = u'user:' + user + u'/Text')
    safe_put(page, text, comment = u'extract text')

    return ret_val(E_OK, "")


def safe_put(page,text,comment):
    if re.match("^[\s\n]*$", text):
        return

    # FIXME, why this is protected by a lock ? if it is only for the setAction,
    # pass the comment directly to put, but is put() thread safe? Actually not
    # a trouble, only one instance of the bot can run but better to check that
    mylock.acquire()
    wikipedia.setAction(comment)

    while 1:
        try:
            status, reason, data = page.put(text)
            if reason != u'OK':
                print "put error", status, reason, data
                time.sleep(10)
                continue
            else:
                break
        except wikipedia.LockedPage:
            print "put error : Page %s is locked?!" % page.aslink().encode("utf8")
            break
        except wikipedia.NoPage:
            print "put error : Page does not exist %s" % page.aslink().encode("utf8")
            break
        except pywikibot.NoUsername:
            print "put error : No user name on wiki %s" % page.aslink().encode("utf8")
            break
        except:
            print "put error: unknown exception"
            time.sleep(5)
            break
    mylock.release()


extract_queue = []

def html_for_queue(queue):
    html = ''
    for i in queue:
        mtitle = i[0].decode('utf-8')
        codelang = i[1]
        try:
            # FIXME: do not harcode the family
            msite = wikipedia.getSite(codelang, 'wikisource')
            page = wikipedia.Page(msite, mtitle)
            path = msite.nice_get_address(page.urlname())
            url = '%s://%s%s' % (msite.protocol(), msite.hostname(), path)
        except:
            url = ""
        html += date_s(i[3])+' '+i[2]+" "+i[1]+" <a href=\""+url+"\">"+i[0]+"</a><br/>"
    return html

# title user lang t conn
def do_status(lock, queue):
    lock.acquire()
    queue = copy.copy(queue)
    lock.release()

    html = common_html.get_head('Extract text layer')
    html += "<body><div>The robot is running.<br/><hr/>"
    html += "<br/>%d jobs in extract queue.<br/>" % len(queue)
    html += html_for_queue(queue)
    html += '</div></body></html>'
    return html

def bot_listening(lock):

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('', 12345))
    except:
        print "could not start listener : socket already in use"
        thread.interrupt_main()
        return

    print date_s(time.time())+ " START"
    sock.listen(1)
    sock.settimeout(None)

    # The other side needs to know the server name where the daemon run to open
    # the connection. We write it after bind() because we want to ensure than
    # only one instance of the daemon is running. FIXME: this is not sufficient
    # if the job is migrated so migration is disabled for this daemon.
    servername_filename = '/home/phe/public_html/extract_text_layer.server'
    if os.path.exists(servername_filename):
        os.chmod(servername_filename, 0644)
    fd = open(servername_filename, "w")
    fd.write(socket.gethostname())
    fd.close()
    os.chmod(servername_filename, 0444)

    try:
        while True:
            conn, addr = sock.accept()
            data = conn.recv(1024)
            try:
                cmd, title, lang, user = data.split('|')
            except:
                print "error", data
                conn.close()
                continue

            t = time.time()
            user = user.replace(' ', '_')

            print date_s(t) + " REQUEST " + user + ' ' + lang + ' ' + cmd + ' ' + title

            if cmd == "extract":
                add_job(lock, extract_queue, (title, lang, user, t, conn))
            elif cmd == 'status':
                html = do_status(lock, extract_queue)
                conn.sendall(html);
                conn.close()
            else:
                conn.sendall(json.dumps(ret_val(E_ERROR, "unknown command: " + cmd)));
                conn.close()

    finally:
        sock.close()
        print "STOP"

def date_s(at):
    t = time.gmtime(at)
    return "[%02d/%02d/%d:%02d:%02d:%02d]"%(t[2],t[1],t[0],t[3],t[4],t[5])


def job_thread(lock, queue, func):
    while True:
        title, codelang, user, t, conn = get_job(lock, queue)

        time1 = time.time()
        out = ''
        try:
            mysite = wikipedia.getSite(codelang, config.family)
        except:
            out = ret_val(E_ERROR, "site error: " + repr(codelang))
            mysite = False

        if mysite:
            wikipedia.setSite(mysite)
            print mysite, title
            title = title.decode('utf-8')
            user = user.decode('utf-8')
            out = func(mysite, title, user, codelang)

        if conn:
            conn.sendall(json.dumps(out))
            conn.close()

        time2 = time.time()
        print date_s(time2) + title.encode('utf-8') + ' ' + user.encode("utf8") + " " + codelang + " (%.2f)" % (time2-time1) + " " + str(out)

        remove_job(lock, queue)


if __name__ == "__main__":
    lock = thread.allocate_lock()
    thread.start_new_thread(job_thread, (lock, extract_queue, do_extract))
    bot_listening(lock)
