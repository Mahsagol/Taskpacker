import itertools as itt
import numpy as np
import Numberjack as nj


def numberjack_scheduler(work_units, upper_bound=500,
                         optimize=True, time_limit=5,
                         solver_method="Mistral"):
    """Makes an optimized schedule for the workunits.

    Examples
    --------

    >>>
    >>>
    >>>
    >>>

    Parameters
    -----------

    work_units
      A list of WorkUnits to be scheduled

    upper_bound
      Upper bound for the time. The unit depends on the unit
      chosen for the duration of the work unit's tasks.

    optimize
      If false, any solution satisfying the constraints (including deadlines)
      will be returned. But sometimes it is not possible to respect all
      deadlines. If True, the function will try to return a schedule which
      minimizes the days over the deadlines. The function minimized is the sum
      of (wu.priority*wu.delay) for all work units with a due time.

    time_limit
      Time in seconds after which the optimizer stops. If the optimizer stops
      because of this time limit the solution may not be optimal.

    solver_method
      The solver used by NumberJack (see NumberJack docs).

    """

    ZERO = nj.Variable([0])
    lower_bound = 0

    all_tasks = [task for work_unit in work_units
                 for task in work_unit.tasks]

    # Create Numberjack variables to represent the tasks
    # ( starting times and resource instance that they use).
    nj_tasks = {
        task: nj.Task(upper_bound, task.duration)
        for task in all_tasks
    }

    nj_taskresources = {
        task: {resource: nj.Variable(1, resource.capacity)
               for resource in task.resources}
        for task in all_tasks
    }

    all_resources = list(set([
        resource
        for task in all_tasks
        for resource in task.resources
    ]))

    model = nj.Model()

    for resource in all_resources:
        if resource.capacity == 1:
            # The resource has one slot: Only one job at the same time
            model.add(nj.UnaryResource([
                nj_tasks[task] for task in all_tasks
                if (resource in task.resources)
            ]))
        else:
            # The resource has several slots
            tasks_pairs_sharing_resource = [
                (task, other_task)
                for (task, other_task) in itt.combinations(all_tasks, 2)
                if ((resource in task.resources) and
                    (resource in other_task.resources))
            ]
            for (task, other_task) in tasks_pairs_sharing_resource:
                model.add(nj.Or([
                    nj.Or([
                        nj_tasks[task] + nj_tasks[task].duration <=
                        nj_tasks[other_task],
                        nj_tasks[other_task] + nj_tasks[other_task].duration <=
                        nj_tasks[task]
                    ]),
                    nj.AllDiff([
                        nj_taskresources[task][resource],
                        nj_taskresources[other_task][resource]
                    ])
                ]))

    # The tasks in one work unit are executed after one another
    model.add([
        (nj_tasks[task] + task.duration <= nj_tasks[next_task])
        for work_unit in work_units
        for (task, next_task) in zip(work_unit.tasks, work_unit.tasks[1:])
    ])

    # Additional constraints inside each work unit, used to e.g.
    # specify that task 2 should be executed at most 10min after
    # task 1 completes.
    model.add([
        constraint([nj_tasks[task] for task in work_unit.tasks])
        for work_unit in work_units
        for constraint in work_unit.constraints
    ])

    # Enforce precedence between inter-dependent work units:
    # If WU1 depends on WU2, then the first task in WU2 must
    # be executed after the last work unit in W1.
    model.add([
        (nj_tasks[parent_work_unit.tasks[-1]] +
         parent_work_unit.tasks[-1].duration) <=
        nj_tasks[work_unit.tasks[0]]
        for work_unit in work_units
        for parent_work_unit in work_unit.parents
    ])

    # If some tasks have a scheduled start time set, enforce it
    model.add([
        nj.Eq([nj_tasks[work_unit.tasks[0]], work_unit.scheduled_start])
        for work_unit in work_units
        if work_unit.scheduled_start is not None
    ])

    # C_max is defined as the end time of the last task executed
    # [nj_tasks[work_unit.tasks[-1]] < C_max for work_unit in work_units],
    # Formula is
    # sum ( 1000 * work_unit.priority * delay
    #       for work_unit with due time in work units)
    # Plus a penalty just to compress a little more:
    # sum ( work_unit.t_end for all work_units)
    if optimize:
        C_max = nj.Variable(lower_bound, 1500000, 'C_max')
        model.add(
            C_max >
            sum([
                nj.Max([ZERO, nj_tasks[work_unit.tasks[-1]] +
                        work_unit.tasks[-1].duration -
                        work_unit.due_time]) *
                (1000*work_unit.priority)
                for work_unit in work_units
                if work_unit.due_time is not None
            ]) +
            sum([
                (nj_tasks[work_unit.tasks[-1]] +
                 work_unit.tasks[-1].duration)
                for work_unit in work_units
            ])
        )

        # Specify that the goal is to minimize C_max (compress the schedule).
        model.add(nj.Minimize(C_max))

    else:
        model.add([
            nj_tasks[work_unit.tasks[-1]] + work_unit.tasks[-1].duration <
            work_unit.due_time
            for work_unit in work_units
            if work_unit.due_time is not None
        ])

    solver = model.load(solver_method)
    solver.setVerbosity(True)
    solver.setTimeLimit(time_limit)
    result = solver.solve()

    if result is False:
        raise ValueError("No solution found by the schedule optimizer !")

    for task in all_tasks:
        nj_task = nj_tasks[task]
        start = nj_task.get_value()
        end = start + task.duration
        resources = {resource: nj_taskresources[task][resource].get_value()
                     for resource in task.resources}
        task.scheduled = (start, end, resources)

    return work_units


def plot_schedule(work_units):
    """ Plot the work units schedule in a gant-like way.

    This is quite basic and arbitrary and really meant for R&D purposes.

    Example
    -------

    >>> # ... Make some work units
    >>> solve_constraints(work_units)
    >>> plot_schedule(work_units)

    """

    try:
        import matplotlib.pyplot as plt
        import matplotlib.cm as cm
    except:
        raise ImportError("Plotting requires Matplotlib.")

    all_resources = list(set([
        resource
        for work_unit in work_units
        for task in work_unit.tasks
        for resource in task.resources
    ]))

    colors = itt.cycle([cm.Paired(0.21*i % 1.0) for i in range(30)])
    fig, ax = plt.subplots(1, figsize=(15, 6))
    max_end = 0
    for work_unit in work_units:
        color = colors.next()
        label = work_unit.name
        for task in work_unit.tasks:
            start, end, resources = task.scheduled
            max_end = max(end, max_end)
            for y in [all_resources.index(resource) +
                      0.2 + .6*resources[resource]/resource.capacity
                      for resource in task.resources]:
                ax.plot([start, end], [y, y], color=color, lw=10, label=label)
                label = None

    strips_colors = itt.cycle([(1, 1, 1), (1, 0.92, 0.92)])
    for i, color in zip(range(-1, len(all_resources)+1), strips_colors):
        ax.fill_between([0, 1.1*max_end], [i, i], y2=[i+1, i+1], color=color)

    N = len(all_resources)
    ax.set_yticks(np.arange(N)+0.5)
    ax.set_ylim(-max(1, int(0.4*N)), max(2, int(1.5*N)))
    ax.set_yticklabels(all_resources)
    ax.legend(ncol=3, fontsize=8)
    ax.set_xlabel("time (a.u.)")
    ax.set_xlim(0, 1.1*max_end)

    return fig, ax
