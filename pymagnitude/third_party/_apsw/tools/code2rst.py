# Python
#
# See the accompanying LICENSE file.
#
# Extracts rst function comments and dumps them to a file

import sys
import os
import re
import urllib2
import tempfile
import collections
import copy

if len(sys.argv)!=4:
    print >> sys.stderr, "You must supply sqlite version, input and output filenames"

if os.path.exists(sys.argv[3]):
    os.remove(sys.argv[3])

basesqurl="https://sqlite.org"+(sys.argv[1]=="fossil" and "/draft/" or "/")

op=[]
op.append(".. Automatically generated by code2rst.py")
op.append("   code2rst.py %s %s" % (sys.argv[2], sys.argv[3]))
op.append("   Edit %s not this file!" % (sys.argv[2],))
op.append("")
if sys.argv[2]!="src/apsw.c":
    op.append(".. currentmodule:: apsw")
    op.append("")

import apsw

with tempfile.NamedTemporaryFile() as f:
    f.write(urllib2.urlopen(basesqurl+"toc.db").read())
    f.flush()

    db=apsw.Connection(f.name)

    funclist={}
    consts=collections.defaultdict(lambda: copy.deepcopy({"vars": []}))
    const2page={}

    for name, type, title, uri in db.cursor().execute("select name, type, title, uri from toc"):
        if type=="function":
            funclist[name]=basesqurl+uri
        elif type=="constant":
            const2page[name]=basesqurl+uri
            consts[title]["vars"].append(name)
            consts[title]["page"]=basesqurl+uri.split("#")[0]

def do_mappings():
    maps=mappings.keys()
    maps.sort()

    seenmappings=set()
    for map in maps:
        # which page does this correspond to?
        m=mappings[map]

        foundin=set()
        for val in m:
            # present in multiple mappings
            if val in {"SQLITE_OK", "SQLITE_IGNORE", "SQLITE_ABORT"}:
                continue
            for desc, const in consts.items():
                if val in const["vars"]:
                    foundin.add(desc)

        assert len(foundin)==1
        desc=list(foundin)[0]
        seenmappings.add(desc)

        # check to see if apsw is missing any
        shouldexit=False
        lookfor=set(consts[desc]["vars"])

        for v in lookfor:
            if v not in mappings[map]:
                print "Mapping", map, "is missing", v
                shouldexit=True
        if shouldexit:
            sys.exit(1)

        op.append(map+" `"+desc+" <"+consts[desc]["page"]+">`__")
        op.append("")

        vals=m[:]
        vals.sort()
        op.append("")
        op.append("    %s" % (", ".join(["`%s <%s>`__" % (v, const2page[v]) for v in vals]),))
        op.append("")

    ignores={
        "Compile-Time Library Version Numbers",
        "Constants Defining Special Destructor Behavior",
        "Function Flags",
        "Fundamental Datatypes",
        "Maximum xShmLock index",
        "Mutex Types",
        "Prepared Statement Scan Status Opcodes",
        "Status Parameters for prepared statements",
        "Testing Interface Operation Codes",
        "Text Encodings",
    }

    for d in sorted(consts.keys()):
        if d not in seenmappings and d not in ignores:
            print "Missing mapping", d, "with values", consts[d]["vars"], "at", consts[d]["page"]


# we have our own markup to describe what sqlite3 calls we make using
# -* and then a space separated list.  Maybe this could just be
# automatically derived from the source?
def do_calls(line):
    line=line.strip().split()
    assert line[0]=="-*"
    indexop=["", ".. index:: "+(", ".join(line[1:])), ""]
    saop=["", "Calls:"]

    calls=[]

    for func in line[1:]:
        calls.append("`%s <%s>`__"% (func, funclist[func]))

    if len(calls)==1:
        saop[-1]=saop[-1]+" "+calls[0]
    else:
        for c in calls:
            saop.append("  * "+c)

    saop.append("")
    return indexop, saop


def do_methods():
    # special handling for __init__ - add into class body
    i="__init__"
    if i in methods:
        v=methods[i]
        del methods[i]
        dec=v[0]
        p=dec.index(i)+len(i)
        sig=dec[p:]
        body=v[1:]
        indexop, saop=[],[]
        newbody=[]
        for line in body:
            if line.strip().startswith("-*"):
                indexop, saop=do_calls(line)
            else:
                newbody.append(line)
        body=newbody
        for j in range(-1, -9999, -1):
            if op[j].startswith(".. class::"):
                for l in indexop:
                    op.insert(j,l)
                op[j]=op[j]+sig
                break
        op.append("")
        op.extend(body)
        op.append("")
        op.extend(fixup(op, saop))
        op.append("")

    keys=methods.keys()
    keys.sort()

    for k in keys:
        op.append("")
        d=methods[k]
        dec=d[0]
        d=d[1:]
        indexop=[]
        saop=[]
        newd=[]
        for line in d:
            if line.strip().startswith("-*"):
                indexop, saop=do_calls(line)
            else:
                newd.append(line)

        d=newd

        # insert index stuff
        op.extend(indexop)
        # insert classname into dec
        if curclass:
            dec=re.sub(r"^(\.\.\s+(method|attribute)::\s+)()", r"\1"+curclass+".", dec)
        if "automethod" in dec and "main()" in dec and 'SQLITE_VERSION_NUMBER' in keys:
            # we have to 'automethod' main ourselves since sphinx is too stupid
            # to get the module right
            op.append(".. method:: main()\n")
            import imp
            op.extend(imp.load_source("apswshell", "tools/shell.py").main.__doc__.split("\n"))
        else:
            op.append(dec)
            op.extend(d)
        op.append("")
        op.extend(fixup(op, saop))

# op is current output, integrate is unindented lines that need to be
# indented correctly for output
def fixup(op, integrate):
    if len(integrate)==0:
        return []
    prefix=999999
    for i in range(-1, -99999, -1):
        if op[i].startswith(".. "):
            break
        if len(op[i].strip())==0:
            continue
        leading=len(op[i])-len(op[i].lstrip())
        prefix=min(prefix, leading)
    return [" "*prefix+line for line in integrate]

methods={}

curop=[]

cursection=None

incomment=False
curclass=None

if sys.argv[2]=="src/apsw.c":
    mappingre=re.compile(r'\s*(ADDINT\s*\(\s*([^)]+)\).*|DICT\s*\(\s*"([^"]+)"\s*\)>*)')
    mappings={}
else:
    mappings=None

for line in open(sys.argv[2], "rtU"):
    line=line.rstrip()
    if mappings is not None:
        m=mappingre.match(line)
        if m:
            g=m.groups()
            if g[2]:
                curmapping=g[2]
                mappings[curmapping]=[]
            else:
                mappings[curmapping].append(g[1])

    if not incomment and line.lstrip().startswith("/**"):
        # a comment intended for us
        line=line.lstrip(" \t/*")
        cursection=line
        incomment=True
        assert len(curop)==0
        if len(line):
            t=line.split()[1]
            if t=="class::":
                if methods:
                    do_methods()
                    methods={}
                curclass=line.split()[2].split("(")[0]
        curop.append(line)
        continue
    # end of comment
    if incomment and line.lstrip().startswith("*/"):
        op.append("")
        incomment=False
        line=cursection
        if len(line):
            t=cursection.split()[1]
            if t in ("automethod::", "method::", "attribute::", "data::"):
                name=line.split()[2].split("(")[0]
                methods[name]=curop
            elif t=="class::":
                op.append("")
                op.append(curclass+" class")
                op.append("="*len(op[-1]))
                op.append("")
                op.extend(curop)
            # I keep forgetting double colons
            elif t.endswith(":") and not t.endswith("::"):
                raise Exception("You forgot double colons: "+line)
            else:
                if methods:
                    import pdb ; pdb.set_trace()
                assert not methods # check no outstanding methods
                op.extend(curop)
        else:
            do_methods()
            methods={}
            op.extend(curop)
        curop=[]
        continue
    # ordinary comment line
    if incomment:
        curop.append(line)
        continue

    # ignore everything else


if methods:
    do_methods()

if mappings:
    do_mappings()

# remove double blank lines
op2=[]
for i in range(len(op)):
    if i+1<len(op) and len(op[i].strip())==0 and len(op[i+1].strip())==0:
        continue
    if len(op[i].strip())==0:
        op2.append("")
    else:
        op2.append(op[i].rstrip())
op=op2

open(sys.argv[3], "wt").write("\n".join(op)+"\n")