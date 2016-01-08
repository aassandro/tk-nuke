# Copyright (c) 2013 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

import os
import sys
import copy

import nuke
import tank

from tank import TankError

class StudioContextSwitcher(object):
    """
    A Toolkit context-switching manager.
    """

    def __init__(self, engine):
        """
        Initializes a StudioContextSwitcher object.

        :param engine:  The running sgtk.engine.Engine to associate the
                        context switcher with.
        """
        self._event_desc = [
            dict(
                add=nuke.addOnCreate,
                remove=nuke.removeOnCreate,
                registrar=nuke.callbacks.onCreates,
                function=self._startup_node_callback,
            ),
            dict(
                add=nuke.addOnScriptSave,
                remove=nuke.removeOnScriptSave,
                registrar=nuke.callbacks.onScriptSaves,
                function=self._on_save_callback,
            ),
        ]

        self._engine = engine
        self._context_cache = dict()
        self._init_project_root = engine.tank.project_path
        self._init_context = engine.context
        self._current_studio_context = False

        self.register_events(reregister=True)

    ##########################################################################
    # properties

    @property
    def context(self):
        """
        The current sgtk.context.Context.
        """
        if self.engine:
            self._context = self.engine.context
        return self._context

    @property
    def current_studio_context(self):
        """
        Whether Nuke Studio is in "Hiero" or "Nuke" mode presently.
        """
        return ['Hiero', 'Nuke'][self._current_studio_context]

    @property
    def engine(self):
        """
        The current engine that is running.
        """
        if not self._engine:
            self._engine = tank.platform.current_engine()
        return self._engine

    @property
    def init_context(self):
        """
        The sgtk.context.Context that was used at initialization time.
        """
        return self._init_context

    @property
    def init_project_root(self):
        """
        The project root directory path at initialization time.
        """
        return self._init_project_root

    ##########################################################################
    # private

    def _change_context(self, new_context):
        """
        Changes Toolkit's context, or creates a disabled menu item if
        that is not possible.

        :param new_context: The sgtk.context.Context to change to.
        """
        # try to create new engine
        try:
            tank.platform.change_context(new_context)
        except tank.TankEngineInitError, e:
            # Context was not sufficient!
            self.engine.menu_generator.create_sgtk_disabled_menu(e)

    def _check_if_registered(self, func, registrar):
        """
        Checks if a callback is already registered with Nuke Studio.
        """
        # The test is made by comparing the name of the functions.
        # see: http://docs.thefoundry.co.uk/nuke/90/pythondevguide/callbacks.html
        for nodeClass_category in registrar.values():
            for (function, args, kwargs, nodeClass) in nodeClass_category:
                if func.__name__ == function.__name__:
                    return True
        return False

    def _eventHandler(self, event):
        """
        Event handler for context switching events in Nuke Studio.

        :param event:   The Nuke Studio event that was triggered.
        """
        focusInNuke = event.focusInNuke

        # Testing if we actually changed context or if the event got fired without
        # the user switching to the node graph. Early exit if it's still the
        # same context.
        if self._current_studio_context == focusInNuke:
            return

        # Set the current context to be remembered for the next context
        # change.
        self._current_studio_context = focusInNuke

        if focusInNuke:
            # We switched from the project timeline to a Nuke node graph.
            try:
                script_path = nuke.scriptName()
            except Exception:
                script_path = None

            if script_path:
                # Switched to nuke with a script open. We have a path and could try
                # to figure out the sgtk context from that.
                new_context = self._get_new_context(script_path)

                if new_context is not None and new_context != self.engine.context:
                    self._change_context(new_context)
            else:
                # There is no script open in the nuke node graph. Empty
                # session so we won't switch to a new context, although we
                # should rebuild the menu to reflect that.
                self.engine.menu_generator.create_sgtk_disabled_menu()
        else:
            # This is a switch back to the project-level timeline,
            # so change to that context based on that project file's
            # path.
            project_path = self._get_current_project()
            if project_path is not None:
                self._change_context(self._get_new_context(project_path))

    def _get_context_from_script(self, script):
        """
        Returns an sgtk.context.Context object from the given script path.

        :param script:  The path to a script file on disk.
        """
        tk = tank.tank_from_path(script)

        context = tk.context_from_path(
            script,
            previous_context=self.engine.context,
        )

        if context.project is None:
            raise tank.TankError(
                "The Nuke engine needs at least a project "
                "context in order to start! Your context: %s" % context
            )
        else:
            return context

    def _get_current_project(self):
        """
        Returns the current project based on where in the UI the user clicked.
        """
        import hiero.core
        import hiero.ui

        view = hiero.ui.activeView()
        if isinstance(view, hiero.ui.TimelineEditor):
            sequence = view.sequence()
            project = sequence.binItem().project()
            return project.path()
        else:
            return None

    def _get_new_context(self, script_path):
        """
        Returns a new sgtk.context.Context for the given script path.

        If the context exists in the in-memory cache, then that is returned,
        otherwise a new Context object is constructed, cached, and returned.

        :param script_path: The path to a script file on disk.
        """
        context = self._context_cache.get(script_path)

        if context:
            return context

        try:
            context = self._get_context_from_script(script_path)
            if context:
                self._context_cache[script_path] = context
                return context
            else:
                raise tank.TankError(
                    'Toolkit could not determine the context associated with this script.'
                )
        except Exception, e:
            self.engine.menu_generator.create_sgtk_disabled_menu(e)
            self.engine.log_debug(e)

        return None

    def _on_save_callback(self):
        """
        Callback that fires every time a file is saved.
        """
        try:
            # Get the new file name.
            file_name = nuke.root().name()
            try:
                # This file could be in another project altogether, so 
                # create a new Tank instance.
                tk = tank.tank_from_path(file_name)
            except tank.TankError, e:
                self.engine.menu_generator.create_sgtk_disabled_menu(e)
                return

            # Extract a new context based on the file and change to that
            # context.
            new_context = tk.context_from_path(
                file_name,
                previous_context=self.context,
            )

            self._change_context(new_context)
        except Exception:
            self.engine.menu_generator.create_sgtk_error_menu()

    def _startup_node_callback(self):
        """
        Callback that fires every time a node gets created.
        """
        try:
            # Look for the root node. This is created only when a new or existing
            # file is opened.
            if nuke.thisNode() != nuke.root():
                return

            if nuke.root().name() == "Root":
                # This is a file->new call, so base it on the context we
                # stored from the previous session.
                tk = tank.Tank(self.init_project_root)

                if self.init_context:
                    new_ctx = self.init_context
                else:
                    new_ctx = tk.context_empty()
            else:
                # This is a file->open call, so we can get the new context
                # from the file path that was opened.
                file_name = nuke.root().name()
                try:
                    tk = tank.tank_from_path(file_name)
                except tank.TankError, e:
                    self.engine.menu_generator.create_sgtk_disabled_menu(e)
                    return

                new_ctx = tk.context_from_path(
                    file_name,
                    previous_context=self.context,
                )

            # Now change the context for the engine and apps.
            self._change_context(new_ctx)
        except Exception, e:
            self.engine.menu_generator.create_sgtk_error_menu(e)

    ##########################################################################
    # public

    def destroy(self):
        """
        Tears down the context switcher by deregistering event handlers.
        """
        self.unregister_events()

    def register_events(self, reregister=False):
        """
        Registers context-switching event handlers with Nuke Studio.

        :param reregister:  If True, previously-registered event handlers will
                            be removed and new instances of those handlers will
                            be reregistered with Nuke Studio. If False, any
                            event handler that has already been registered with
                            Nuke Studio will be skipped.
        """
        import hiero.core

        # Event for context switching from Hiero to Nuke.
        hiero.core.events.registerInterest(
            hiero.core.events.EventType.kContextChanged,
            self._eventHandler,
        )

        for func_desc in self._event_desc:
            # This is the variable that stores a dict of currently-registered
            # callbacks.
            registrar = func_desc.get('registrar')

            # The function we wish to register.
            function = func_desc.get('function')

            # The function used to register the callback.
            add = func_desc.get('add')

            # Check if the callback is already registered.
            if self._check_if_registered(function, registrar):
                if reregister:
                    self._unregister_events(only=[func_desc])
                else:
                    continue

            add(function)

    def unregister_events(self):
        """
        Unregisters any event handlers that the context switcher
        created during a register_events call.
        """
        import hiero.core

        hiero.core.events.unregisterInterest(
            hiero.core.events.EventType.kContextChanged,
            self._eventHandler,
        )

        func_descs = only or self._event_desc

        for func_desc in func_descs:
            registrar = func_desc.get('registrar')

            # The function we wish to unregister.
            function = func_desc.get('function')

            # The function used to unregister the callback.
            remove = func_desc.get('remove')

            if self._check_if_registered(function, registrar):
                remove(function)

