# Interface to gurobipy

from warnings import warn
from copy import deepcopy
from itertools import izip

from gurobipy import Model, LinExpr, GRB, QuadExpr


from ..core.Solution import Solution

solver_name = 'gurobi'
_SUPPORTS_MILP = True


# set solver-specific parameters
parameter_defaults = {'objective_sense': 'maximize',
                      'tolerance_optimality': 1e-6,
                      'tolerance_feasibility': 1e-6,
                      'tolerance_integer': 1e-9,
                      # This is primal simplex, default is -1 (automatic)
                      'lp_method': 0,
                      'log_file': '',
                      'tolerance_barrier': 1e-8}
parameter_mappings = {'log_file': 'LogFile',
                      'lp_method': 'Method',
                      'threads': 'Threads',
                      'objective_sense': 'ModelSense',
                      'output_verbosity': 'OutputFlag',
                      'quadratic_precision': 'Quad',
                      'time_limit': 'TimeLimit',
                      'tolerance_feasibility': 'FeasibilityTol',
                      'tolerance_markowitz': 'MarkowitzTol',
                      'tolerance_optimality': 'OptimalityTol',
                      'iteration_limit': 'IterationLimit',
                      'MIP_gap_abs': 'MIPGapAbs',
                      'MIP_gap': 'MIPGap'}
variable_kind_dict = {'continuous': GRB.CONTINUOUS, 'integer': GRB.INTEGER}
sense_dict = {'E': GRB.EQUAL, 'L': GRB.LESS_EQUAL, 'G': GRB.GREATER_EQUAL}
objective_senses = {'maximize': GRB.MAXIMIZE, 'minimize': GRB.MINIMIZE}
status_dict = {GRB.OPTIMAL: 'optimal', GRB.INFEASIBLE: 'infeasible',
               GRB.UNBOUNDED: 'unbounded', GRB.TIME_LIMIT: 'time_limit'}

def get_status(lp):
    status = lp.status
    if status in status_dict:
        status = status_dict[status]
    else:
        status = 'failed'
    return status

def get_objective_value(lp):
    return lp.ObjVal

def format_solution(lp, cobra_model, **kwargs):
    status = get_status(lp)
    if status not in ('optimal', 'time_limit'):
        the_solution = Solution(None, status=status)
    else:
        objective_value = lp.ObjVal
        x = [v.X for v  in lp.getVars()]      
        x_dict = {r.id: value for r, value in izip(cobra_model.reactions, x)}
        if lp.isMIP:
            y = y_dict = None #MIP's don't have duals
        else:
            y = [c.Pi for c in lp.getConstrs()]
            y_dict = {m.id: value for m, value in izip(cobra_model.metabolites, y)}
        the_solution = Solution(objective_value, x=x, x_dict=x_dict, y=y,
                                y_dict=y_dict, status=status)
    return(the_solution)

def set_parameter(lp, parameter_name, parameter_value):
    if parameter_name == 'ModelSense' or parameter_name == "objective_sense":
        lp.setAttr(parameter_name, objective_senses[parameter_value])
    else:
        parameter_name = parameter_mappings.get(parameter_name, parameter_name)
        lp.setParam(parameter_name, parameter_value)

def change_variable_bounds(lp, index, lower_bound, upper_bound):
    variable = lp.getVarByName(str(index))
    variable.lb = lower_bound
    variable.ub = upper_bound


def change_variable_objective(lp, index, objective):
    variable = lp.getVarByName(str(index))
    variable.obj = objective


def change_coefficient(lp, met_index, rxn_index, value):
    met = lp.getConstrByName(str(met_index))
    rxn = lp.getVarByName(str(rxn_index))
    lp.chgCoeff(met, rxn, value)


def update_problem(lp, cobra_model, **kwargs):
    """A performance tunable method for updating a model problem file

    lp: A gurobi problem object

    cobra_model: the cobra.Model corresponding to 'lp'

    """
    #When reusing the basis only assume that the objective coefficients or bounds can change
    try:
        quadratic_component = kwargs['quadratic_component']
        if quadratic_component is not None:
            warn("update_problem does not yet take quadratic_component as a parameter")
    except:
        quadratic_component = None

    if 'copy_problem' in kwargs and kwargs['copy_problem']:
        lp = lp.copy()
    if 'reuse_basis' in kwargs and not kwargs['reuse_basis']:
        lp.reset()
    for the_variable, the_reaction in zip(lp.getVars(),
                                          cobra_model.reactions):
        the_variable.lb = float(the_reaction.lower_bound)
        the_variable.ub = float(the_reaction.upper_bound)
        the_variable.obj = float(the_reaction.objective_coefficient)


def create_problem(cobra_model, quadratic_component=None, **kwargs):
    """Solver-specific method for constructing a solver problem from
    a cobra.Model.  This can be tuned for performance using kwargs


    """
    lp = Model("")
    #Silence the solver
    set_parameter(lp, 'OutputFlag', 0)

    the_parameters = parameter_defaults
    if kwargs:
        the_parameters = deepcopy(parameter_defaults)
        the_parameters.update(kwargs)

    [set_parameter(lp, parameter_mappings[k], v)
         for k, v in the_parameters.iteritems() if k in parameter_mappings]


    # Create variables
    #TODO:  Speed this up
    variable_list = [lp.addVar(float(x.lower_bound),
                               float(x.upper_bound),
                               float(x.objective_coefficient),
                               variable_kind_dict[x.variable_kind],
                               str(i))
                     for i, x in enumerate(cobra_model.reactions)]
    reaction_to_variable = dict(zip(cobra_model.reactions,
                                    variable_list))
    # Integrate new variables
    lp.update()

    #Constraints are based on mass balance
    #Construct the lin expression lists and then add
    #TODO: Speed this up as it takes about .18 seconds
    #HERE
    for i, the_metabolite in enumerate(cobra_model.metabolites):
        constraint_coefficients = []
        constraint_variables = []
        for the_reaction in the_metabolite._reaction:
            constraint_coefficients.append(the_reaction._metabolites[the_metabolite])
            constraint_variables.append(reaction_to_variable[the_reaction])
        #Add the metabolite to the problem
        lp.addConstr(LinExpr(constraint_coefficients, constraint_variables),
                     sense_dict[the_metabolite._constraint_sense.upper()],
                     the_metabolite._bound,
                     str(i))

    # Set objective to quadratic program
    if quadratic_component is not None:
        set_quadratic_objective(lp, quadratic_component)

    lp.update()
    return(lp)


def set_quadratic_objective(lp, quadratic_objective):
    if not hasattr(quadratic_objective, 'todok'):
        raise Exception('quadratic component must have method todok')
    variable_list = lp.getVars()
    linear_objective = lp.getObjective()
    # If there already was a quadratic expression set, this will be quadratic
    # and we need to extract the linear component
    if hasattr(linear_objective, "getLinExpr"):  # duck typing
        linear_objective = linear_objective.getLinExpr()
    gur_quadratic_objective = QuadExpr()
    for (index_0, index_1), the_value in quadratic_objective.todok().items():
        # gurobi does not multiply by 1/2 (only does v^T Q v)
        gur_quadratic_objective.addTerms(the_value * 0.5,
                                         variable_list[index_0],
                                         variable_list[index_1])
    # this adds to the existing quadratic objectives
    lp.setObjective(gur_quadratic_objective + linear_objective)

def solve_problem(lp, **kwargs):
    """A performance tunable method for updating a model problem file

    """
    #Update parameter settings if provided
    if kwargs:
        [set_parameter(lp, parameter_mappings[k], v)
         for k, v in kwargs.iteritems() if k in parameter_mappings]

    lp.update()
    lp.optimize()
    status = get_status(lp)
    return status

    
def solve(cobra_model, **kwargs):
    """

    """
    #Start out with default parameters and then modify if
    #new onese are provided
    the_parameters = deepcopy(parameter_defaults)
    if kwargs:
        the_parameters.update(kwargs)
    for i in ["new_objective", "update_problem", "the_problem"]:
        if i in the_parameters:
            raise Exception("Option %s removed" % i)
    if 'error_reporting' in the_parameters:
        warn("error_reporting deprecated")

    #Create a new problem
    lp = create_problem(cobra_model, **the_parameters)


    ###Try to solve the problem using other methods if the first method doesn't work
    try:
        lp_method = the_parameters['lp_method']
    except:
        lp_method = 0
    the_methods = [0, 2, 1]
    if lp_method in the_methods:
        the_methods.remove(lp_method)
    #Start with the user specified method
    the_methods.insert(0, lp_method)
    for the_method in the_methods:
        the_parameters['lp_method'] = the_method
        try:
            status = solve_problem(lp, **the_parameters)
        except:
            status = 'failed'
        if status == 'optimal':
            break

    the_solution = format_solution(lp, cobra_model)
    #if status != 'optimal':
    #    print '%s failed: %s'%(solver_name, status)
    #cobra_model.solution = the_solution
    #solution = {'the_problem': lp, 'the_solution': the_solution}
    return the_solution
