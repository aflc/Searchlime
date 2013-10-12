import sublime, sublime_plugin
import os
import threading
import sys
import fnmatch
import time

wsh = None
def plugin_loaded():
    global wsh
    import importlib
    whoosh_libdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'whoosh_2_5_4')
    wsh_loader = importlib.find_loader('whoosh', [whoosh_libdir])
    wsh = wsh_loader.load_module()
    import whoosh.index
    import whoosh.fields
    import whoosh.qparser
    import whoosh.query
    global SPLIT_LINENUM, SCHEMA
    SPLIT_LINENUM = 10000
    SCHEMA = wsh.fields.Schema(path=wsh.fields.ID(stored=True), mtime=wsh.fields.STORED, fsize=wsh.fields.STORED,
                               data=wsh.fields.NGRAM(stored=True, phrase=True, minsize=1, maxsize=2),
                               line_offset=wsh.fields.STORED)


def is_enabled(window):
    # local switch
    project_data = window.project_data()
    if 'Searchlime' in project_data:
        if 'enable' in project_data['Searchlime']:
            return bool(project_data['Searchlime']['enable'])
    # global switch
    settings = sublime.load_settings('Searchlime.sublime-settings')
    if settings.get("enable", False):
        return True
    return False

def load_options(window):
    # loading
    settings = sublime.load_settings('Searchlime.sublime-settings')
    global_settings = sublime.load_settings('Preferences.sublime-settings')
    options = {}
    options['indexdir'] = settings.get('indexdir')
    options['binary'] = global_settings.get('binary_file_patterns', [])
    options['exclude_files'] = global_settings.get('file_exclude_patterns', [])
    options['exclude_dirs'] = global_settings.get('folder_exclude_patterns', [])
    # update options with Searchlime settings
    options['binary'] += settings.get('binary_file_patterns', [])
    options['exclude_files'] += settings.get('file_exclude_patterns', [])
    options['exclude_dirs'] += settings.get('folder_exclude_patterns', [])
    # update options with project settings
    project_settings = window.project_data().get('Searchlime', {})
    options['indexdir'] = project_settings.get('indexdir', options['indexdir'])
    options['binary'] += project_settings.get('binary_file_patterns', [])
    options['exclude_files'] += project_settings.get('file_exclude_patterns', [])
    options['exclude_dirs'] += project_settings.get('folder_exclude_patterns', [])
    # merge duplicated patterns
    options['binary'] = set(options['binary'])
    options['exclude_files'] = set(options['exclude_files'])
    options['exclude_dirs'] = set(options['exclude_dirs'])
    # dirs in project
    options['folders'] = []
    project_dir = os.path.dirname(window.project_file_name())
    for d in window.project_data().get('folders', []):
        options['folders'].append({'path': os.path.join(project_dir, d['path']), 'follow_symlinks':d.get('follow_symlinks', False)})
    # post process
    options['indexdir'] = os.path.expanduser(options['indexdir'])
    if not os.path.exists(options['indexdir']):
        os.makedirs(options['indexdir'])
    return options


def readfile(path):
    try:
        data = open(path, encoding='utf-8').readlines()
        for i in range(0, len(data), SPLIT_LINENUM):
            yield i * SPLIT_LINENUM, ''.join(data[i:i+SPLIT_LINENUM])
    except UnicodeDecodeError:
        pass


def readdata(view):
    data = view.substr(sublime.Region(0, view.size())).splitlines()
    for i in range(0, len(data), SPLIT_LINENUM):
        yield i * SPLIT_LINENUM, '\n'.join(data[i:i+SPLIT_LINENUM])


def update_index(ix, paths, callback=None):
    with ix.searcher() as searcher:
        with ix.writer(limitmb=256) as writer:
            for path in paths:
                fstat = os.stat(path)
                mtime, fsize = fstat.st_mtime_ns, fstat.st_size
                doc = searcher.document(path=path)
                if not doc or doc['mtime'] != mtime or fsize != doc['fsize']:
                    writer.delete_by_term('path', path)
                    for line_offset, data in readfile(path):
                        writer.add_document(path=path, data=data, line_offset=line_offset, mtime=mtime, fsize=fsize)
                if callback:
                    callback()


def update_index_with_view(ix, view):
    with ix.searcher() as searcher:
        with ix.writer(limitmb=256) as writer:
            path = view.file_name()
            fstat = os.stat(path)
            mtime, fsize = fstat.st_mtime_ns, fstat.st_size
            doc = searcher.document(path=path)
            if not doc or doc['mtime'] != mtime or fsize != doc['fsize']:
                writer.delete_by_term('path', path)
                for line_offset, data in readdata(view):
                    writer.add_document(path=path, data=data, line_offset=line_offset, mtime=mtime, fsize=fsize)


def open_ix(indexdir, name, create=False, recreate=False):
    if not name:
        return None
    if recreate:
        return wsh.index.create_in(indexdir, SCHEMA, indexname=name)
    elif wsh.index.exists_in(indexdir, indexname=name):
        return wsh.index.open_dir(indexdir, indexname=name)
    elif create:
        return wsh.index.create_in(indexdir, SCHEMA, indexname=name)


class SearchlimeUpdateIndexCommand(sublime_plugin.WindowCommand):
    def __init__(self, window):
        super().__init__(window)
    def run(self):
        if is_enabled(self.window):
            indexThread = threading.Thread(target=self.runIndexing)
            indexThread.start()
        else:
            self.window.active_view().set_status("Searchlime", "Searchlime is disabled")

    def runIndexing(self):
        self.indexing = True
        opts = load_options(self.window)

        self.total_files = 0
        self.num_files = 0
        projectname = os.path.basename(self.window.project_file_name())

        ix = open_ix(opts['indexdir'], projectname, create=True)
        if not ix:
            self.window.active_view().set_status("Searchlime", "indexdir open error")
        paths = list(self.get_files_in_project(opts))
        self.total_files = len(paths)
        self.updateStatus()
        update_index(ix, paths, callback=self.increment_index_count)
        self.indexing = False
        self.window.active_view().erase_status("Searchlime")

    def increment_index_count(self):
        self.num_files += 1

    def get_files_in_project(self, opts):
        visited = set()
        for fdata in opts['folders']:
            for root, dirs, filenames in os.walk(fdata['path'], followlinks=fdata['follow_symlinks']):
                # remove dirs
                for idx, d in enumerate(dirs):
                    if d in visited:
                        dirs[idx] = None
                    if self.match_pattern(os.path.basename(d), opts['exclude_dirs']):
                        dirs[idx] = None
                while None in dirs:
                    dirs.remove(None)
                visited.update(set(dirs))
                for name in filenames:
                    if not self.match_pattern(name, opts['exclude_files'] | opts['binary']):
                        yield os.path.join(root, name)

    def match_pattern(self, s, patterns):
        for pat in patterns:
            if fnmatch.fnmatch(s, pat):
                return True
        return False

    def updateStatus(self):
        if self.indexing:
            percent = 100.0
            if self.total_files > 0:
                percent = self.num_files / self.total_files * 100
            self.window.active_view().set_status(
                "Searchlime",
                "Searchlime indexing {}/{} files({} %)".format(self.num_files, self.total_files, int(percent)))
            sublime.set_timeout(self.updateStatus, 2000)


class SearchlimeReindexCommand(SearchlimeUpdateIndexCommand):
    def __init__(self, window):
        super().__init__(window)

    def runIndexing(self):
        self.indexing = True
        opts = load_options(self.window)

        self.total_files = 0
        self.num_files = 0
        projectname = os.path.basename(self.window.project_file_name())

        # reindex whole project
        ix = open_ix(opts['indexdir'], projectname, recreate=True)
        if not ix:
            self.window.active_view().set_status("Searchlime", "indexdir open error")
        paths = list(self.get_files_in_project(opts))
        self.total_files = len(paths)
        self.updateStatus()
        update_index(ix, paths, callback=self.increment_index_count)
        self.indexing = False
        self.window.active_view().erase_status("Searchlime")


class SearchlimeSearchCommand(sublime_plugin.WindowCommand):
    def __init__(self, window):
        sublime_plugin.WindowCommand.__init__(self, window)
        self.searching = False
        self.search_for = ""
        self.active_view = None

    def run(self):
        view = self.window.active_view()
        selectionText = view.substr(view.sel()[0])
        if is_enabled(self.window):
            self.window.show_input_panel("Searchlime:", selectionText or self.search_for, self.search, None, None)
            return
        sublime.error_message("Searchlime disabled")

    def search(self, search_for):
        self.searching = True
        self.search_for = search_for
        codeSearchThread = threading.Thread(target=self.run_search)
        codeSearchThread.start()

    def run_search(self):
        projectname = os.path.basename(self.window.project_file_name())
        opts = load_options(self.window)
        if wsh.index.exists_in(opts['indexdir'], indexname=projectname):
            ix = wsh.index.open_dir(opts['indexdir'], indexname=projectname)
        else:
            sublime.error_message("Searchlime indexdir not founed")
            return
        self.items = []
        parser = wsh.qparser.QueryParser('data', ix.schema)
        with ix.searcher() as searcher:
            query = parser.parse('"{}"'.format(self.search_for))
            for hit in searcher.search(query):
                gotpath = hit.get('path')
                data = hit.get('data')
                for linenum, line in self.search_lines(data):
                    linenum += hit.get('line_offset')
                    self.items.append(['{} [{}]'.format(line.strip(), os.path.basename(gotpath)), '{}:{}'.format(linenum, gotpath)])
                if len(self.items) > 100000:
                    break
        self.current_view = self.window.active_view()
        if len(self.items) > 0:
            self.window.show_quick_panel(self.items, self.on_done, 0, 0, self.on_highlighted)
        else:
            self.window.show_quick_panel(["No results"], self.on_done_none)


    def search_lines(self, data):
        idx = 0
        while True:
            idx = data.find(self.search_for, idx)
            if idx == -1:
                break
            else:
                startofline = data.rfind('\n', 0, idx)
                if startofline == -1:
                    startofline = 0
                else:
                    startofline += 1
                endofline = data.find('\n', idx + len(self.search_for))
                linenum = data.count('\n', 0, startofline) + 1
                yield linenum, data[startofline:endofline]
                if endofline == -1:
                    break
                else:
                    idx = endofline + 1

    def on_done(self, index):
        if index == -1:
            self.window.focus_view(self.current_view)
            flush_key(self.current_view)
        else:
            item = self.items[index]
            linenum, path = item[1].rsplit(':', 1)
            linenum = int(linenum)
            view = self.window.open_file(path)
            if self.active_view and self.active_view != view:
                flush_key(self.active_view)
            self.active_view = view
            move_to_view_thread = threading.Thread(target=self.move_to_view, args=(self.active_view, linenum))
            move_to_view_thread.start()

    def move_to_view(self, view, linenum):
        rg = self.show_view(view, linenum, highlight=False)
        sel = view.sel()
        sel.clear()
        sel.add(rg)
        flush_key(view)

    def show_view(self, view, linenum, highlight=True):
        while view.is_loading():
            time.sleep(0.05)
        topl = view.rowcol(view.visible_region().begin())[0]
        bottoml = view.rowcol(view.visible_region().end())[0]
        height = bottoml - topl
        pt = view.text_point(linenum - 1, 0)
        if highlight:
            highlight_searchword(view, self.search_for)
            view.add_regions('Searchlime_line', [view.line(pt)], 'comment', '', sublime.DRAW_NO_FILL)
        csr_region = view.find(self.search_for, pt)
        view.add_regions('Searchlime_csr', [csr_region], 'string', '', sublime.DRAW_NO_FILL)
        topl = view.rowcol(view.visible_region().begin())[0]
        view.show_at_center(view.text_point(linenum - height / 4, 0))
        return csr_region

    def on_done_none(self, index):
        flush_key(self.current_view)

    def on_highlighted(self, index):
        if index != -1:
            item = self.items[index]
            linenum, path = item[1].split(':', 1)
            linenum = int(linenum)
            view = self.window.open_file(path, sublime.TRANSIENT)
            if self.active_view and self.active_view != view:
                flush_key(self.active_view)
            self.active_view = view
            show_view_thread = threading.Thread(target=self.show_view, args=(self.active_view, linenum))
            show_view_thread.start()
            view.set_status("Searchlime", "search: {} found: {} files ".format(self.search_for, len(self.items)))
        else:
            self.window.focus_view(self.current_view)
            flush_key(self.current_view)


def highlight_searchword(view, word):
    regions = view.get_regions('Searchlime_regions')
    if not regions:
        regions = view.find_all(word)
    if len(regions) > 0:
        view.add_regions("Searchlime_regions", regions, "entity.name.filename.find-in-files", "dot", sublime.DRAW_OUTLINED)
        view.set_status("Searchlime_regions", "regions: " + str(len(regions)))

def flush_key(view):
    view.erase_status("Searchlime")
    view.erase_status("Searchlime_regions")
    view.erase_regions("Searchlime_line")
    view.erase_regions("Searchlime_csr")
    view.erase_regions("Searchlime_regions")


class SearchlimeUpdateEvent(sublime_plugin.EventListener):
    current_project = None
    current_ix = None

    def on_load_async(self, view):
        window = view.window()
        if self.change_state(window):
            window.run_command('searchlime_update_index')

    def on_post_save_async(self, view):
        if not self.current_project:
            self.change_state(view.window())
        if self.current_ix:
            update_index_with_view(self.current_ix, view)

    def change_state(self, window):
        projectname = os.path.basename(window.project_file_name())
        if projectname and projectname != self.current_project:
            current_project = projectname
            opts = load_options(window)
            self.current_ix = open_ix(opts['indexdir'], projectname)
            return True
        return False
